import paramiko
import matplotlib
import pandas as pd
import numpy as np
from io import StringIO
import os
import h5py
# from fabric import Connection
from io import BytesIO
import glob
import numpy as np
import re
import tempfile
import matplotlib.pyplot as plt
from dataclasses import replace
from pathlib import Path

from waveform_storage.io.hdf5_io import (
    read_single_device_hdf5,
    write_alignment_v2,
    write_legacy_merged_hdf5,
)
from waveform_storage.processing.alignment import (
    AlignmentParameters,
    DeviceSeries,
    SkippedDevice,
    align_devices,
)

DEFAULT_STORAGE_HOST = os.environ.get("WAVEFORM_STORAGE_HOST", "192.0.2.2")
DEFAULT_STORAGE_USER = os.environ.get("WAVEFORM_STORAGE_USER", "storage-user")
DEFAULT_STORAGE_PASSWORD = os.environ.get("WAVEFORM_STORAGE_PASSWORD")


class AlignmentResultCache:
    """Cache one aligned GUI query so confirm, plot, and export can share it."""

    def __init__(self):
        self._key = None
        self._result = None

    def clear(self):
        self._key = None
        self._result = None

    def get_or_load(self, key, loader):
        if self._key != key or self._result is None:
            self._result = loader()
            self._key = key
        return self._result

def get_para(time_sec):

    time_sec=time_sec.replace(':',"")
    print('get time_sec:',time_sec)

    return time_sec

def creat_dir(_path):
    if not os.path.exists(_path):
        os.makedirs(_path)
        print(f'creat diretory:{_path}')

def remote_save_timesec_h5_files(hostname, username, password, remote_path, local_path,time_sec, port=22):
    creat_dir(_path=local_path)
    try:
        # 创建SSH客户端
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname, port=port, username=username, password=password)
        command = f"find {remote_path} -name '*{time_sec}*.h5' -type f"
        sftp = ssh.open_sftp()
        stdin, stdout, stderr = ssh.exec_command(command)
        file_list = stdout.read().decode().splitlines()
        for filename1 in file_list:
            filename = filename1.rsplit('/',1)[-1]
            remote_filename_path = os.path.join(remote_path, filename)
            local_filename_path = os.path.join(local_path,filename)
            try:
                sftp.get(remote_filename_path,local_filename_path)
            except:
                print('wrong in save h5 file')
    except:
        print('something wrong in connect')

def remote_search_time_sec(hostname, username, password, remote_path):
    # remote server
    numbers = set()
    try:

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname, username=username, password=password)
        command = f"find {remote_path} -name '*.h5' -type f"
        stdin, stdout, stderr = ssh.exec_command(command)
        file_list = stdout.read().decode().splitlines()
        for file_path in file_list:
            try:

                filename = os.path.basename(file_path)
                number = filename.split('-')[1]
                numbers.add(number)
            except (IndexError, AttributeError):
                continue

    except Exception as e:
        print(f"连接错误: {e}")
        return []
    finally:
        if 'ssh' in locals():
            ssh.close()

    time_sec_list = sorted(list(numbers))
    return time_sec_list


def remote_find_h5_files(hostname, username, password, remote_path, time_sec, port=22):
    h5_file_name = []
    matching_files = []
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname, port=port, username=username, password=password)
        command = f"find {remote_path} -name '*{time_sec}*.h5' -type f"
        #print(f"执行命令: {command}")
        stdin, stdout, stderr = ssh.exec_command(command)
        file_list = stdout.read().decode().splitlines()
        #print(f'file_list{file_list}')
        error_output = stderr.read().decode()
        if error_output:
            print(f"错误信息: {error_output}")
        # Keep paths and parsed device IDs paired.  Ignoring a malformed name
        # must not shift every later file onto the wrong device.
        for file_path in file_list:
            print(file_path)
            filename = file_path.split("/")[-1]
            match = re.search(r'RAW-\d+-(.*?)-S\d+\.h5', filename)
            if match:
                device_name = match.group(1)
                matching_files.append(file_path)
                h5_file_name.append(device_name)
    except Exception as e:
        print(f"连接错误: {e}")
        return [], []
    finally:
        if 'ssh' in locals():
            ssh.close()

    return matching_files, h5_file_name

def read_h5(h5_file):
    hostname = DEFAULT_STORAGE_HOST
    username = DEFAULT_STORAGE_USER
    password = DEFAULT_STORAGE_PASSWORD
    try:

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 连接到服务器
        ssh.connect(hostname, port=22, username=username, password=password)
        sftp = ssh.open_sftp()
        rows = len(h5_file)
        #print("number of .h5 files:", rows)
        x_list = [[] for _ in range(rows)]
        y_list = [[] for _ in range(rows)]
        bunchID_list = [[] for _ in range(rows)]

        for i in range(len(h5_file)):
            file = h5_file[i]
            #print(f"\n处理文件: {os.path.basename(file)}")
            try:
                with tempfile.NamedTemporaryFile(suffix='.h5', delete=True) as temp_file:
                    # 下载远程文件到本地临时文件
                    sftp.get(file, temp_file.name)
                    with h5py.File(temp_file.name, 'r') as f:

                        BunchIDData_dset = f['BunchIDData'][0]
                        bunchID_data = BunchIDData_dset[:]
                        bunchID_list[i].append(bunchID_data)

                        Data_dset = f['Data']
                        x_data = Data_dset[0, :]
                        y_data = Data_dset[1, :]

                        # print("origin bunchID:", bunchID_data, len(bunchID_data))
                        # print("origin x_data:", x_data, len(x_data))
                        # print("origin y_data:", y_data, len(y_data))

                        x = x_data.tolist()
                        y = y_data.tolist()

                        x_list[i].append(x)
                        y_list[i].append(y)

            except Exception as file_error:
                print(f"处理文件 {file} 时出错: {file_error}")
                continue

        if not any(x_list) or not any(y_list) or not any(bunchID_list):
            print("没有成功读取任何文件数据")
            sftp.close()
            ssh.close()
            return [], [], []


        bunchID_2d = np.vstack([sublist[0] for sublist in bunchID_list if sublist]).tolist()
        x_list_2d = np.vstack([sublist[0] for sublist in x_list if sublist]).tolist()
        y_list_2d = np.vstack([sublist[0] for sublist in y_list if sublist]).tolist()

        sftp.close()
        ssh.close()

        return x_list_2d, y_list_2d, bunchID_2d

    except Exception as e:
        print(f"SSH连接或处理过程中出错: {e}")
        # 确保资源被正确关闭
        try:
            sftp.close()
        except:
            pass
        try:
            ssh.close()
        except:
            pass
        return [], [], []




def read_h5_device_series(file_device_pairs):
    """Download single-device files and preserve file/device association."""

    if not file_device_pairs:
        return [], []

    hostname = DEFAULT_STORAGE_HOST
    username = DEFAULT_STORAGE_USER
    password = DEFAULT_STORAGE_PASSWORD
    records = []
    skipped = []
    ssh = None
    sftp = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname, port=22, username=username, password=password)
        sftp = ssh.open_sftp()
        for remote_file, expected_device_id in file_device_pairs:
            try:
                with tempfile.NamedTemporaryFile(suffix=".h5", delete=True) as temp_file:
                    sftp.get(remote_file, temp_file.name)
                    source = read_single_device_hdf5(temp_file.name)
                if source.device_id != expected_device_id:
                    raise ValueError(
                        f"file metadata device {source.device_id!r} does not match "
                        f"filename device {expected_device_id!r}"
                    )
                records.append(
                    DeviceSeries(
                        device_id=expected_device_id,
                        bunch_ids=source.bunch_ids,
                        x=source.x,
                        y=source.y,
                    )
                )
            except Exception as exc:
                skipped.append(
                    SkippedDevice(expected_device_id, "read_failed", str(exc))
                )
    finally:
        if sftp is not None:
            sftp.close()
        if ssh is not None:
            ssh.close()
    return records, skipped


def main_alignment_process(
    remote_path,
    runID,
    time_sec,
    pv_list_sorted,
    parameters=None,
):
    """Retrieve one GUI query and return the shared AlignmentResult."""

    hostname = DEFAULT_STORAGE_HOST
    username = DEFAULT_STORAGE_USER
    password = DEFAULT_STORAGE_PASSWORD
    h5_files, device_ids = remote_find_h5_files(
        hostname,
        username,
        password,
        remote_path,
        time_sec,
    )
    order = {device_id: index for index, device_id in enumerate(pv_list_sorted)}
    known_pairs = []
    skipped = []
    for remote_file, device_id in zip(h5_files, device_ids):
        if device_id not in order:
            skipped.append(
                SkippedDevice(
                    device_id,
                    "unknown_device",
                    "device is absent from the configured full device list",
                )
            )
            continue
        known_pairs.append((remote_file, device_id))
    known_pairs.sort(key=lambda item: order[item[1]])

    records, read_skipped = read_h5_device_series(known_pairs)
    result = align_devices(
        records,
        parameters or AlignmentParameters(mode="strict"),
    )
    return replace(
        result,
        skipped_devices=tuple(skipped) + tuple(read_skipped) + result.skipped_devices,
    )


def alignment_summary(result):
    return f"{result.bunch_ids.size} are overlapped!"


def alignment_bunch_id(result, selected_index):
    if result.bunch_ids.size == 0:
        raise ValueError("the alignment result contains no bunchID")
    index = min(max(int(selected_index), 0), result.bunch_ids.size - 1)
    return result.bunch_ids[index]


def plot_alignment_result(result, selected_index):
    """Plot one bunchID directly from AlignmentResult and its validity mask."""

    bunch_id = alignment_bunch_id(result, selected_index)
    index = min(max(int(selected_index), 0), result.bunch_ids.size - 1)
    valid = result.valid_mask[:, index]
    devices = np.asarray(result.device_ids, dtype=object)[valid]
    plot_x = result.x[:, index][valid]
    plot_y = result.y[:, index][valid]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(devices, plot_x, marker="o", label="X", linewidth=2)
    ax.plot(devices, plot_y, marker="s", label="Y", linewidth=2)
    ax.set_title(f"BunchID:{bunch_id} <-----> Index:{index}")
    ax.set_xticks(range(len(devices)))
    ax.set_xticklabels(devices, rotation=90)
    ax.set_xlabel("Devicename")
    ax.set_ylabel("Value")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.7)
    fig.tight_layout()
    return fig


def save_alignment_result(
    result,
    runID,
    time_sec,
    save_path,
    export_format="auto",
):
    """Export one shared result as legacy or v2 without realigning it."""

    selected_format = str(export_format).strip().lower()
    if selected_format not in {"auto", "legacy", "v2"}:
        raise ValueError("export_format must be one of: auto, legacy, v2")
    if selected_format == "auto":
        selected_format = "legacy" if np.all(result.valid_mask) else "v2"

    destination_dir = Path(save_path)
    if selected_format == "legacy":
        destination = destination_dir / f"{runID}_{time_sec}_BPMdata.h5"
        return str(write_legacy_merged_hdf5(destination, result))

    destination = destination_dir / f"{runID}_{time_sec}_BPMdata_v2.h5"
    return str(
        write_alignment_v2(
            destination,
            result,
            metadata={"run_id": runID, "time_sec": time_sec},
        )
    )


def get_overlap_indices_matrix(lists_2d):
    if not lists_2d:
        return [], []

    sets_list = []
    skip_ind = []
    for i,sublist in enumerate(lists_2d):
        if 0 in sublist:
            skip_ind.append(i)
        else:

            filtered_set = set(val for val in sublist)
            sets_list.append(filtered_set)

    threshold = int(0.5 * len(sets_list[0]))


    overlap_set = sets_list[0].copy()
    for i in range(1,len(lists_2d)):
        current_dim=set(lists_2d[i])
        temp_set=overlap_set & current_dim

        if len(temp_set) < threshold:
            skip_ind.append(i)
            #print(f"skip overlap:{i}")
            continue

        overlap_set=temp_set
        #print(f"current overlap:{len(overlap_set)},index in list:{i}")
    overlap = sorted(list(overlap_set))
    indices_matrix = []
    lists_2d_new=[]
    for i,sublist in enumerate(lists_2d):
        if i not in skip_ind:
            lists_2d_new.append(sublist.copy())
    for sublist in lists_2d_new:
        indices = []
        for overlap_val in overlap:
            try:
                idx = sublist.index(overlap_val)
            except ValueError:
                idx = -1
            indices.append(idx)
        indices_matrix.append(indices)

    return overlap,skip_ind,indices_matrix

def dataprocess(x_list,y_list,indices_matrix,skip_ind):

    x_list_new=[]
    y_list_new = []
    for i,sublist in enumerate(x_list):
        if i not in skip_ind:
            x_list_new.append(sublist.copy())
    for i,sublist in enumerate(y_list):
        if i not in skip_ind:
            y_list_new.append(sublist.copy())
    # new series with overlap index
    x_new_list = []
    y_new_list = []
    for i, indices in enumerate(indices_matrix):

        x_extracted_values = [x_list_new[i][idx] for idx in indices]
        y_extracted_values = [y_list_new[i][idx] for idx in indices]
        x_new_list.append(x_extracted_values)
        y_new_list.append(y_extracted_values)

    return x_new_list,y_new_list



def create_hdf5(bunchID,x_new,y_new,devicename,runID,time_sec,skip_ind,save_path):
    devicename_new=[]
    for i in range(0,len(devicename)):
        if i not in skip_ind:
            devicename_new.append(devicename[i])

    #print("new device:",devicename_new)
    # Bunch IDs in production are around 10^13.  Keep them as uint64 from
    # conversion through HDF5 serialization; an intermediate int32 conversion
    # silently wrapped valid IDs.
    bunchID_array = np.asarray(bunchID, dtype=np.uint64)  # (len(overlap))
    item_len = len(bunchID_array)
    #print("number of overslap:", len(bunchID_array))
    x_new_array = np.array(x_new, dtype=np.int32)  # (len(h5_file),len(overlap))
    group_len = len(x_new_array)
    #print("number of BPM:", len(x_new_array))

    y_new_array = np.array(y_new, dtype=np.int32)

    h5_newfile = f"{runID}_{time_sec}_BPMdata.h5"
    new_filename = os.path.join(save_path, h5_newfile)
    #print("the newest h5 file path:", new_filename)

    with h5py.File(new_filename, 'w') as f:
        # x_new/y_new contain only devices that survived alignment.  Metadata
        # must use the same filtered order so each pair of Data rows is mapped
        # to the correct device.
        f.attrs['Device'] = devicename_new
        for i in range(0, len(devicename_new)):
            f.attrs[f'Data[0:]_{i}'] = devicename_new[i] + '_X'
            f.attrs[f'Data[1:]_{i}'] = devicename_new[i] + '_Y'

        BunchIDData_dset = f.create_dataset('BunchIDData', shape=(1, item_len), dtype=np.uint64)
        Data_dset = f.create_dataset('Data', shape=(2 * group_len, item_len), dtype=np.int32)
        BunchIDData_dset[:] = bunchID_array.reshape(1, item_len)
        data_combined = np.zeros((2 * group_len, item_len), dtype=np.int32)
        for i in range(group_len):
            data_combined[2 * i, :] = x_new_array[i, :].astype(np.int32)  # x数据
            data_combined[2 * i + 1, :] = y_new_array[i, :].astype(np.int32)  # y数据
        Data_dset[:] = data_combined
        return new_filename


#######################add for plot-start##################
def selectConfirm(bunchID_new_list):
    max_index=len(bunchID_new_list)-1
    min_index=0
    # confirm_str=f"请输入{min_index}至{max_index}之间的整数！"
    confirm_str = f"{max_index+1} are overlapped!"
    confirm_str=str(confirm_str)

    return confirm_str

def showBunchID(selectedIDindex,bunchID_new_list):
    selectedIDindex=int(selectedIDindex)

    if selectedIDindex < 0:
        selectedIDindex = 0
        print("correct selectIndex:",selectedIDindex)
    elif selectedIDindex > len(bunchID_new_list)-1:
        selectedIDindex = len(bunchID_new_list)-1
        print("correct selectIndex:", selectedIDindex)
    else:
        print("selectIndex is right:",selectedIDindex)

    current_bunchID = bunchID_new_list[selectedIDindex]


    return current_bunchID



def plotbunchID(selectedIDindex,bunchID_new_list,x_new_list,y_new_list,devicename,skip_ind):

    devicename_new=[]
    for i in range(0,len(devicename)):
        if i not in skip_ind:
            devicename_new.append(devicename[i])

    plot_x=[]
    plot_y=[]
    selectedIDindex=int(selectedIDindex)




    if selectedIDindex < 0:
        selectedIDindex = 0
        print("correct selectIndex:",selectedIDindex)
    elif selectedIDindex > len(bunchID_new_list)-1:
        selectedIDindex = len(bunchID_new_list)-1
        print("correct selectIndex:", selectedIDindex)
    else:
        print("selectIndex is right:",selectedIDindex)


    for i in range(0,len(x_new_list)):
        plot_x.append(x_new_list[i][selectedIDindex])
        plot_y.append(y_new_list[i][selectedIDindex])


    ###画图，X轴devicename_new，内容plot_x，plot_y
    BunchID=bunchID_new_list[selectedIDindex]
    # 创建DataFrame
    df = pd.DataFrame({
        'Device': devicename_new,
        'X': plot_x,
        'Y': plot_y
    })

    # 绘制图表
    # 创建图形
    fig, ax = plt.subplots(figsize=(10, 6))

    # 绘制折线
    ax.plot(df['Device'], df['X'], marker='o', label='X', linewidth=2)
    ax.plot(df['Device'], df['Y'], marker='s', label='Y', linewidth=2)

    # 设置图表属性
    ax.set_title(f'BunchID:{BunchID} <-----> Index:{selectedIDindex}')
    ax.set_xticks(range(len(df['Device'])))
    ax.set_xticklabels(df['Device'],rotation=90)
    ax.set_xlabel('Devicename')
    ax.set_ylabel('Value')
    ax.legend()
    ax.grid(True, linestyle='--', alpha=0.7)

    # 自动调整布局
    fig.tight_layout()

    return fig




    #################################################add for plot-end##################





def main_dataprocess(remote_path, runID, time_sec,pv_list_sorted):
    print("_____________test begin______")

    hostname = DEFAULT_STORAGE_HOST
    username = DEFAULT_STORAGE_USER
    password = DEFAULT_STORAGE_PASSWORD

    #print("user select time_sec:", time_sec)  # get user put in

    h5_list, filename = remote_find_h5_files(hostname, username, password, remote_path, time_sec)
    # print(f'h5_list--{h5_list}')
    # print(f'filename--{filename}')
    filename_sorted = sorted(filename, key=lambda x: pv_list_sorted.index(x))
    # print(f'filename_sorted--{filename_sorted}')
    #print("path", h5_list)
    #print("All Device name:", filename)
    h5_list_sorted = sorted(h5_list, key=lambda x: filename_sorted.index(x.split('-')[2]))
    # print(f'h5_list_sorted--{h5_list_sorted}')
    x_list, y_list, bunchID_list = read_h5(h5_file=h5_list_sorted)
    bunchID_new_list,skip_ind,indices_matrix=get_overlap_indices_matrix(bunchID_list)
    skip_ind=set(skip_ind)
    Skip_BPMs_names=[]
    for i,devicename in enumerate(filename_sorted):
        # print(f'devicename{devicename}')
        if i in skip_ind:
            Skip_BPMs_names.append(filename_sorted[i])
    # print("the index of skip BPM:",skip_ind)
    # print("Final indices matrix",indices_matrix)
    # print("Final BPM numbers:", len(indices_matrix))
    # print("Final overslap number:", len(indices_matrix[0]))
    # print("Skip BPMs name:", Skip_BPMs_names)



    x_new_list,y_new_list=dataprocess(x_list, y_list, indices_matrix, skip_ind)
    # print("x finnal",x_new_list)
    # print("y_finnal",y_new_list)
    # print("bunchID_finnal:",bunchID_new_list)


    # #
    # new_filename = create_hdf5(bunchID_new_list, x_new_list, y_new_list, filename, runID, time_sec) # filename = devicename



    # with h5py.File(newfilename, 'r') as f:
    #     print("BunchIDData形状:", f['BunchIDData'][:].shape)
    #     print("Data形状:", f['Data'][:].shape)
    #     # print("BunchIDData:", f['BunchIDData'][:])
    #     print("Data:", f['Data'][0:][0])  # x[0],the first [0:] means X, or [1:] means Y
    #     print("Data:", f['Data'][1:][0])  # y[0]
    #     print(f.attrs[f'Data[1:]_0'])

    return Skip_BPMs_names,len(indices_matrix),len(indices_matrix[0]),bunchID_new_list,x_new_list,y_new_list,filename_sorted,runID,time_sec,skip_ind

def save_newfile(bunchID_new_list,x_new_list,y_new_list,filename,runID,time_sec,skip_ind,save_path):

    #save_path = '/home/desktop-user-a/PycharmProjects/pythonProject2/'
    newfilename = create_hdf5(bunchID=bunchID_new_list, x_new=x_new_list, y_new=y_new_list, devicename=filename,
                              runID=runID, time_sec=time_sec, skip_ind=skip_ind, save_path=save_path)
    #print("the final save filename:", newfilename)

    return newfilename

# if __name__ == '__main__':
#     time_sec='20:10:30'
#     time_sec=get_para(time_sec)
#     print(time_sec)
    # runID = '20251005'
    # remote_path = os.path.join("/SHINE_Data/AC/LA/", runID, "raw/BIData/")
    # print("remote_path:", remote_path)
    # time_sec='225713'  #200034
    # Final_BPMs_number,Final_overlap_number,bunchID_new_list,x_new_list,y_new_list,filename,runID,time_sec,skip_ind=main_dataprocess(remote_path, runID, time_sec)
    #
    # new_h5_filename=save_newfile(bunchID_new_list, x_new_list, y_new_list, filename, runID, time_sec, skip_ind, save_path='')
    # plotbunchID()

'''
if __name__ == '__main__':
    list_2d=[
        [1,2,3,4,5,6],
        [2,3,4,5,6,7],
        [3,4,5,6,7,8],
        [4,5,6,7,8,9],
        [5,6,7,8,9,10]

    ]
    x_list=[
        [1.1,1.2,1.3,1.4,1.5,1.6],
        [2.1, 2.2, 2.3, 2.4, 2.5, 2.6],
        [3.1, 3.2, 3.3, 3.4, 3.5, 3.6],
        [4.1, 4.2, 4.3, 4.4, 4.5, 4.6],
        [5.1, 5.2, 5.3, 5.4, 5.5, 5.6],

    ]

    y_list=[
        [1.11, 1.21, 1.31, 1.41, 1.51, 1.61],
        [2.11, 2.21, 2.31, 2.41, 2.51, 2.61],
        [3.11, 3.21, 3.31, 3.41, 3.51, 3.61],
        [4.11, 4.21, 4.31, 4.41, 4.51, 4.61],
        [5.11, 5.21, 5.31, 5.41, 5.51, 5.61],



    ]
    devicename=['test1','test2','test3','test4','test5',]


    overlap,skip_ind,indices_matrix=get_overlap_indices_matrix(list_2d)
    print(overlap)
    print(skip_ind)
    print(indices_matrix)

    x_new_list,y_new_list=dataprocess(x_list, y_list, indices_matrix, skip_ind)
    print("x finnal",x_new_list)
    print("y_finnal",y_new_list)

    save_path='/home/desktop-user-a/PycharmProjects/pythonProject2/'
    newfilename=create_hdf5(bunchID=overlap, x_new=x_new_list, y_new=y_new_list, devicename=devicename, runID='runIDtest', time_sec='111', skip_ind=skip_ind, save_path=save_path)
    print("the final save filename:",newfilename)
'''
