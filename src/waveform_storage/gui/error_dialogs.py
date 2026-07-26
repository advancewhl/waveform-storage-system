"""Consistent operator-facing error information and dialogs."""

from __future__ import annotations

from dataclasses import dataclass
from traceback import format_exception

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class OperationError:
    title: str
    summary: str
    stage: str
    code: str
    suggestion: str
    details: str = ""

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        stage: str,
        code: str = "UNEXPECTED_ERROR",
        title: str = "操作失败",
        suggestion: str = "请检查详细信息；确认配置和连接后重试。",
    ) -> "OperationError":
        return cls(
            title=title,
            summary=str(error) or error.__class__.__name__,
            stage=stage,
            code=code,
            suggestion=suggestion,
            details="".join(
                format_exception(type(error), error, error.__traceback__)
            ).strip(),
        )


class OperationErrorDialog(QDialog):
    """A structured dialog with stage, code, guidance, and copyable details."""

    def __init__(
        self,
        error: OperationError,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.error = error
        self.setWindowTitle(error.title)
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setObjectName("operationErrorDialog")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

        heading = QHBoxLayout()
        icon = QLabel("!")
        icon.setObjectName("errorIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(38, 38)
        title = QLabel(error.title)
        title.setObjectName("errorTitle")
        code = QLabel(error.code)
        code.setObjectName("errorCode")
        heading.addWidget(icon)
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(code)
        root.addLayout(heading)

        summary = QLabel(error.summary)
        summary.setObjectName("errorSummary")
        summary.setWordWrap(True)
        root.addWidget(summary)

        context = QFrame()
        context.setObjectName("errorContext")
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(14, 12, 14, 12)
        stage = QLabel(f"失败阶段  ·  {error.stage}")
        stage.setObjectName("errorStage")
        suggestion = QLabel(error.suggestion)
        suggestion.setObjectName("errorSuggestion")
        suggestion.setWordWrap(True)
        context_layout.addWidget(stage)
        context_layout.addWidget(suggestion)
        root.addWidget(context)

        self.details = QPlainTextEdit(error.details or "无附加详细信息。")
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMaximumHeight(180)
        root.addWidget(self.details)

        actions = QHBoxLayout()
        details_button = QPushButton("查看详细信息")
        copy_button = QPushButton("复制错误信息")
        close_button = QPushButton("关闭")
        close_button.setObjectName("primaryButton")
        details_button.clicked.connect(self._toggle_details)
        copy_button.clicked.connect(self._copy)
        close_button.clicked.connect(self.accept)
        actions.addWidget(details_button)
        actions.addWidget(copy_button)
        actions.addStretch(1)
        actions.addWidget(close_button)
        root.addLayout(actions)

        self.setStyleSheet(
            """
            QDialog#operationErrorDialog { background: #f8fafc; }
            QLabel#errorIcon {
                background: #fee2e2; color: #b91c1c; border-radius: 19px;
                font-size: 22px; font-weight: 700;
            }
            QLabel#errorTitle { color: #0f172a; font-size: 19px; font-weight: 700; }
            QLabel#errorCode {
                background: #fff1f2; color: #be123c; border: 1px solid #fecdd3;
                border-radius: 10px; padding: 4px 10px; font-weight: 600;
            }
            QLabel#errorSummary { color: #334155; font-size: 14px; }
            QFrame#errorContext {
                background: #ffffff; border: 1px solid #e2e8f0;
                border-radius: 10px;
            }
            QLabel#errorStage { color: #475569; font-weight: 650; }
            QLabel#errorSuggestion { color: #64748b; }
            QPlainTextEdit {
                background: #0f172a; color: #dbeafe; border: 0;
                border-radius: 8px; padding: 8px;
                font-family: Consolas, monospace;
            }
            QPushButton {
                min-height: 32px; padding: 0 13px; border-radius: 7px;
                border: 1px solid #cbd5e1; background: #ffffff; color: #334155;
            }
            QPushButton:hover { background: #f1f5f9; }
            QPushButton#primaryButton {
                background: #2563eb; color: white; border-color: #2563eb;
            }
            """
        )

    def _toggle_details(self) -> None:
        self.details.setVisible(not self.details.isVisible())
        self.adjustSize()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(
            "\n".join(
                (
                    self.error.title,
                    f"错误码: {self.error.code}",
                    f"阶段: {self.error.stage}",
                    f"原因: {self.error.summary}",
                    f"建议: {self.error.suggestion}",
                    "",
                    self.error.details,
                )
            ).strip()
        )


def show_operation_error(
    parent: QWidget | None,
    error: OperationError,
) -> int:
    return OperationErrorDialog(error, parent).exec_()
