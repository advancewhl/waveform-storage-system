"""Local HTTP job API with allowlisted inputs and bounded result downloads."""

from dataclasses import fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID
import json

from .models import AnalysisConfig, AnalysisRequest


ARTIFACTS = {"results.zip", "results.json", "run-manifest.json", "report.md",
             "spectrum-summary.csv", "spectrum-blocks.csv", "data-quality.csv", "spectra.npz"}


def make_server(jobs, allowed_roots, address=("127.0.0.1", 8770)):
    roots = tuple(Path(p).expanduser().resolve(strict=True) for p in allowed_roots)
    if not roots:
        raise ValueError("请指定允许读取的输入根目录")

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                parts = urlparse(self.path).path.strip("/").split("/")
                if len(parts) == 5 and parts[:3] == ["api", "analysis", "jobs"] and parts[4] == "cancel":
                    UUID(parts[3])
                    jobs.cancel(parts[3])
                    self._json(200, jobs.get(parts[3]))
                    return
                if parts != ["api", "analysis", "jobs"]:
                    self._json(404, {"error": "接口不存在"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("请求大小无效")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or data.get("source_type") not in {"archiver", "hdf5"}:
                    raise ValueError("source_type 必须为 archiver 或 hdf5")
                paths = data.get("paths")
                if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
                    raise ValueError("paths 必须是非空路径列表")
                resolved = []
                for value in paths:
                    p = Path(value).expanduser().resolve(strict=True)
                    if not any(p == r or p.is_relative_to(r) for r in roots):
                        raise PermissionError("输入路径不在允许目录内")
                    # Do not follow nested links outside an allowed root during recursive scans.
                    if p.is_dir():
                        for child in p.rglob("*"):
                            if child.is_symlink():
                                target = child.resolve(strict=True)
                                if not any(target == r or target.is_relative_to(r) for r in roots):
                                    raise PermissionError("输入目录包含指向允许目录外的链接")
                    resolved.append(str(p))
                overrides = data.get("config", {})
                valid_fields = {f.name for f in fields(AnalysisConfig)} - {"profile"}
                if not isinstance(overrides, dict) or set(overrides) - valid_fields:
                    raise ValueError("分析配置字段无效")
                config = AnalysisConfig.for_profile(data.get("profile", "high_band"), **overrides)
                request = AnalysisRequest(data["source_type"], tuple(resolved), config,
                                          data.get("sample_rate_hz"))
                job_id = jobs.submit(request)
                self._json(202, jobs.get(job_id))
            except PermissionError as exc:
                self._json(403, {"error": str(exc)})
            except KeyError:
                self._json(404, {"error": "任务不存在"})
            except (ValueError, TypeError, OSError) as exc:
                self._json(400, {"error": str(exc)})

        def do_GET(self):
            try:
                parts = urlparse(self.path).path.strip("/").split("/")
                if parts == ["health"]:
                    self._json(200, {"status": "ok", "service": "spectrum-analysis"})
                    return
                if len(parts) not in {4, 6} or parts[:3] != ["api", "analysis", "jobs"]:
                    self._json(404, {"error": "接口不存在"})
                    return
                UUID(parts[3])
                state = jobs.get(parts[3])
                if len(parts) == 4:
                    state.pop("traceback", None)
                    self._json(200, state)
                    return
                if parts[4] != "artifacts" or parts[5] not in ARTIFACTS:
                    self._json(404, {"error": "结果文件不存在"})
                    return
                if state["status"] != "succeeded":
                    self._json(409, {"error": "任务尚未成功完成"})
                    return
                path = jobs.output_root / parts[3] / parts[5]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Content-Disposition", 'attachment; filename="' + parts[5] + '"')
                self.end_headers()
                with path.open("rb") as stream:
                    while block := stream.read(1024 * 1024):
                        self.wfile.write(block)
            except KeyError:
                self._json(404, {"error": "任务不存在"})
            except (ValueError, OSError) as exc:
                self._json(400, {"error": str(exc)})

    return ThreadingHTTPServer(address, Handler)
