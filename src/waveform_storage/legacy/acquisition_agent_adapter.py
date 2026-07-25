"""Unmodified-behavior acquisition prototype retained as a legacy adapter."""

import paramiko
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import datetime
import logging

import json
import numpy as np
import h5py
import os
import h5py
from pathlib import Path
import glob
import zmq

from caproto.threading.client import Context, Batch
from waveform_storage.config.settings import AppSettings

# pyinstaller - -onefile - n 'saveApp' - -exclude - module PySide6 main_file_transport_h5df.py


class file_transport():
    def __init__(self, max_workers=17):

        self.max_workers = max_workers
        self.ip_addr = {}
        self.hour_minite_secend = 0
        self.year_month_day = 0
        self.localpath = ''
        self.project_root = Path(__file__).resolve().parents[3]
        self.settings = AppSettings.from_env(project_root=self.project_root)
        self.config_dir = self.settings.config_dir
        self.remote_dir = self.settings.device_remote_dir
        self.ssh_username = self.settings.device_ssh_user
        self.ssh_password = self.settings.device_ssh_password
        self.caput_timestr =  int(time.time())

    def start_server(self,host="0.0.0.0", port=5000):
        context = zmq.Context()
        socket = context.socket(zmq.PULL)
        socket.bind(f"tcp://{host}:{port}")
        print("waiting...")
        try:
            while True:
                message = socket.recv_string()
                print(message)
                return message
        except:
            print("stopped")
        finally:
            socket.close()
            context.term()

    def get_pv_list(self):
        pv_list = []
        with open(self.config_dir / 'acquisition_pv_list.txt', 'r', encoding='utf-8') as file:
            for line_number, line in enumerate(file, 1):
                line = line.strip().replace('},', '}')
                if not line:
                    continue
                dict_data = json.loads(line)
                pv_list.append(dict_data['pv'])
        return pv_list

    def startBPMs(self):
        pv_list = self.get_pv_list()
        os.environ['EPICS_CA_ADDR_LIST'] = self.get_addr_list_BPM()
        ctx = Context()
        pvs = ctx.get_pvs(*pv_list)
        for pv in pvs:
            pv.wait_for_connection(
                timeout=self.settings.epics_connect_timeout_seconds
            )  # legacy default: 10s
        with Batch() as b:
            for pv in pvs:
                b.write(pv, 0)
                b.write(pv, 1)
        time.sleep(5)
        with Batch() as b:
            for pv in pvs:
                b.write(pv, 0)
        self.caput_timestr = int(time.time())

    def get_addr_list_BPM(self):
        dict_list = []
        addr_list = ""
        with open(self.config_dir / 'acquisition_pv_list.txt', 'r', encoding='utf-8') as file:
            for line_number, line in enumerate(file, 1):
                line = line.strip().replace('},', '}')
                if not line:
                    continue
                dict_data = json.loads(line)
                addr_list = addr_list + dict_data['ip'] + ':' + dict_data['port'] + ' '
        print(f'addr_list{addr_list}')
        return addr_list

    def get_duration_value(self):
        with open(self.config_dir / 'duration_seconds.txt', 'r', encoding='utf-8') as file:
            duration = int(file.read())
            print(duration)
        return duration

    def get_addr_list(self):
        dict_list = []
        with open(self.config_dir / 'acquisition_pv_list.txt', 'r', encoding='utf-8') as file:
            for line_number, line in enumerate(file, 1):
                line = line.strip().replace('},', '}')
                if not line:
                    continue
                dict_data = json.loads(line)
                dict_list.append(dict_data)
            for item in dict_list:
                item['pv'] = '-'.join(item['pv'].split('-')[:4]).replace(':','_').replace('-','_')
            single_dict_list = []
            for item in dict_list:
                item = {list(item.keys())[1]:list(item.values())[1]}
                single_dict_list.append(item)

            ip_addr_list = []
            for item in dict_list:
                item = {list(item.values())[1]:list(item.values())[0]}
                ip_addr_list.append(item)
        return ip_addr_list,single_dict_list


    def txt_to_h5_basic(self,txt_folder, h5_folder=None, overwrite=False):
        """
        基本批量转换：TXT -> H5

        Args:
            txt_folder: TXT文件所在文件夹
            h5_folder: H5文件输出文件夹（默认同目录）
            overwrite: 是否覆盖已存在的H5文件
        """
        if h5_folder is None:
            h5_folder = txt_folder

        # 确保输出目录存在
        os.makedirs(h5_folder, exist_ok=True)

        # 查找所有TXT文件
        txt_files = glob.glob(os.path.join(txt_folder, "*.txt"))

        if not txt_files:
            print(f"在 {txt_folder} 中没有找到TXT文件")
            return

        print(f"找到 {len(txt_files)} 个TXT文件")

        success_count = 0
        failed_count = 0

        for txt_file in txt_files:
            try:
                # 生成对应的H5文件名
                filename = os.path.basename(txt_file)
                h5_filename = filename.replace('.txt', '.h5')
                h5_filepath = os.path.join(h5_folder, h5_filename)

                # 检查是否已存在
                if os.path.exists(h5_filepath) and not overwrite:

                    continue

                # 读取TXT文件
                with open(txt_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                    lines = [line.strip() for line in content.split('\n') if line.strip()]
                # 2. 初始化三个列表，分别存储三列数据
                    bunchID_data_list = []
                    data_x_list = []
                    data_y_list = []

                # 3. 遍历每行，按空格分割列（支持多个空格），提取三列数据
                    for line in lines:
                    # print(line)
                    # 按任意数量的空格分割（split() 无参数时默认分割所有空白字符）
                        cols = line.split()
                    # 确保每行有三列数据（避免数据格式错误）
                        if len(cols) == 3:
                        # 转为数值类型（根据实际数据选择 int 或 float，这里以 int 为例）
                            datax = int(int(cols[0])/1000)
                            datay = int(int(cols[1])/1000)
                            bunchid = int(cols[2])

                        # 添加到对应列表
                            data_x_list.append(datax)
                            data_y_list.append(datay)
                            bunchID_data_list.append(bunchid)

                data_x = np.array(data_x_list)
                data_y = np.array(data_y_list)
                bunchID_data = np.array(bunchID_data_list)
                # 写入H5文件
                device_name = filename.split('-')[2]
                with h5py.File(h5_filepath, 'w') as h5f:
                    h5f.attrs['Device'] = device_name
                    h5f.attrs['Data_0'] = "bpm_x"
                    h5f.attrs['Data_1'] = "bpm_y"

                    BunchIDData_dset = h5f.create_dataset('BunchIDData', shape=(1, 1024), dtype=np.uint64,
                                                              chunks=(1, 1024))
                    Data_dset = h5f.create_dataset('Data', shape=(2, 1024), dtype=np.int32, chunks=(1, 1024))

                    BunchIDData_dset.id.write_direct_chunk((0, 0), bunchID_data, 0)
                    Data_dset.id.write_direct_chunk((0, 0), data_x.tobytes(), 0)
                    Data_dset.id.write_direct_chunk((1, 0), data_y.tobytes(), 0)
                    h5f.close()

                print(f"✓ 转换成功: {filename} -> {h5_filename}")
                success_count += 1

            except Exception as e:
                print(f"✗ 转换失败 {os.path.basename(txt_file)}: {e}")
                failed_count += 1

        print(f"\n转换完成: 成功 {success_count}, 失败 {failed_count}")







    def create_ssh_client(self,host_config):
        """创建SSH客户端连接"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        hostname111 = host_config['ip']
        print(f'sssh:{hostname111}')
        try:
        # 支持密码和密钥认证
            if host_config.get('key_filename'):
                client.connect(
                    hostname=host_config['ip'],
                    port=host_config.get('port', 22),
                # username=host_config['username'],
                    username=host_config.get('username', self.ssh_username),
                    key_filename=host_config['key_filename'],
                    timeout=self.settings.ssh_connect_timeout_seconds
                )
            else:
                client.connect(
                    hostname=host_config['ip'],
                    port=host_config.get('port', 22),
                # username=host_config['username'],
                    username=host_config.get('username', self.ssh_username),
                    password=self.ssh_password,
                    timeout=self.settings.ssh_connect_timeout_seconds
                )
            return client
        except Exception as e:
            print(f"连接失败 {host_config['ip']}: {e}")
            return None


    def transfer_single_host(self,host_config):
        host_ip = host_config['ip']
        print(host_config)
        ip_list = self.ip_addr
        ssh_client = self.create_ssh_client(host_config=host_config)
        if ssh_client is None:
            print('wrong111')
        try:
            sftp = ssh_client.open_sftp()

            files_name = sftp.listdir(self.remote_dir)
            target_file = f'{files_name[0]}'
            remote_file_path = f'{self.remote_dir}/{target_file}'
            file_start = sftp.stat(remote_file_path)
            modify_last_timestamp = int(file_start.st_mtime)
            # print(f'{modify_last_timestamp}---------------{self.caput_timestr}')
            if modify_last_timestamp > self.caput_timestr:
            # print(f"{remote_file_path}---------------{modify_last_timestamp}----------{self.ip_addr[host_ip]}")
            # modify_last_time = datetime.datetime.fromtimestamp(modify_last_timestamp)
            # print(f"{remote_file_path}---------------{modify_last_time}----------{self.ip_addr[host_ip]}")

                name = f'RAW-{self.hour_minite_secend}-{self.ip_addr[host_ip]}-S00000'
                path = f'{self.localpath}/{name}.txt'
                if target_file in sftp.listdir(self.remote_dir):
                    sftp.get(localpath = path,remotepath =remote_file_path)
                    with sftp.open(remote_file_path, 'r') as f:
                        content = f.read().decode('utf-8')
            else:
                print(f'{self.ip_addr[host_ip]} do not update')

                    # print(content)
        except:
            print(f'wrong{host_ip}')




    def creat_dir(self,_path):
        if not os.path.exists(_path):
            os.makedirs(_path)
            print(f'creat diretory:{_path}')
    def transfer_multiple_hosts(self,hosts_config):
        """并行传输多个主机的文件"""
        max_workers = 17
        start_time = time.time()
        timestr = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        self.hour_minite_secend = timestr[-6:]
        self.year_month_day = timestr[:-6]
        # self.localpath = f'/SHINE_Data/AC/LA/{self.year_month_day}/raw/BIData'
        local_root = str(self.settings.local_data_root)
        self.localpath = os.path.join(
            local_root,
            self.year_month_day,
            "raw",
            "BIData"
        )
        self.creat_dir(self.localpath)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
            future_to_host = {
                executor.submit(self.transfer_single_host, host_config): host_config['ip']
                for host_config in hosts_config
            }

        # 等待所有任务完成
            for future in as_completed(future_to_host):
                host_ip = future_to_host[future]
                try:
                    future.result()
                except Exception as e:
                    print(f"主机 {host_ip} 执行异常: {e}")
        total_duration = time.time() - start_time
        print(f'total_time:{total_duration}')
        self.txt_to_h5_basic(self.localpath, self.localpath)


    def save_h5_data(self):
        host_config_all,hosts_config = self.get_addr_list()
        ip_dict = {key:value for item in host_config_all for key ,value in item.items()}
        self.ip_addr = ip_dict
        self.transfer_multiple_hosts(hosts_config=hosts_config)


    def main(self):
        duration_value = self.get_duration_value()
        # duration_value = int(600)
        while True:
            Frequence = 0
            if Frequence != 0:
                Frequence = 0

            Frequence = int(self.start_server())
            start_time = int(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
            print(f'start_time:{start_time}')
            duration = duration_value
            print(f'start   : {duration} s')

            while True:
                timestr = int(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
                print(f'now time:{timestr}')
                if timestr - start_time > duration:
                    print(f'finish work in {duration} s')
                    break

                self.startBPMs()
                if (Frequence == 100):

                    time.sleep(12)
                    print('stream frequency :100 Hz')
                elif (Frequence == 50):
                    time.sleep(22)
                    print('stream frequency :50 Hz')
                elif (Frequence == 1000):
                    time.sleep(2)
                    print('stream frequency :1000 Hz')
                elif (Frequence == 2):

                    time.sleep(512)
                    print('stream frequency :2 Hz')
                else:
                    print('something wrong')
                self.save_h5_data()
if __name__ == "__main__":
    # 使用基础版本
    aa = file_transport()
    aa.main()
    # aa.save_h5_data()
    # data = aa.get_addr_list_BPM()
    # print(data)
