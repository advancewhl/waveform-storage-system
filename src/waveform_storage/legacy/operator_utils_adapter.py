"""Legacy operator utility implementation retained for import compatibility."""

import os
import json
import numpy as np
import h5py
import time
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"

def get_addr_list():
    dict_list = []
    addr_list = ""
    with open(CONFIG_DIR / 'SWITCH_PV_IP.txt', 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip().replace('},', '}')
            if not line:
                continue
            dict_data = json.loads(line)
            # print(dict_data)
            addr_list = addr_list + dict_data['ip'] + ':' + dict_data['port'] + ' '
            dict_list.append(dict_data)
        combined_dict = {}
        # for d in dict_list:
        #     combined_dict.update(d)
        combined_dict = {k:v for d in dict_list for k,v in d.items()}

        # print(combined_dict)
        # print(addr_list)
        # print(f'dict_list:{dict_list}')
        print(dict_list[1]['ip'])
    return addr_list

def get_ip_list():
    addr_list = ""
    with open(CONFIG_DIR / 'SWITCH_PV_IP.txt', 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip().replace('},', '}')
            if not line:
                continue
            dict_data = json.loads(line)
            # print(dict_data)
            ip_list = addr_list + dict_data['ip']
            print(ip_list)

    return ip_list

def get_pv_list():
    pv_list = []
    with open(CONFIG_DIR / 'SWITCH_PV_IP.txt', 'r', encoding='utf-8') as file:
        for line_number, line in enumerate(file, 1):
            line = line.strip().replace('},', '}')
            if not line:
                continue
            dict_data = json.loads(line)
            pv_list.append(dict_data['pv'])
    return pv_list


def save2hdf5(self, filename):
    """
    direct chunk write strategy
    C:H5Dwrite_chunk, python:wrtie_direct_chunk
    """

    with  h5py.File(filename, 'w') as file:
        file.attrs['Device'] = "bpm_name"
        file.attrs['Data_0'] = "bpm_x"
        file.attrs['Data_1'] = "bpm_y"
        BunchIDData_dset = file.create_dataset('BunchIDData', shape=(1, 1024), dtype=np.uint64, chunks=(1, 1024))
        Data_dset = file.create_dataset('Data', shape=(2, 1024), dtype=np.int32, chunks=(1, 1024))

        bunchID_data = np.random.randint(0, np.iinfo(np.uint64).max, size=1024, dtype=np.uint64)
        data_x = np.random.randint(100, 200, size=1024, dtype=np.int32)
        data_y = np.random.randint(0, 100, size=1024, dtype=np.int32)

        BunchIDData_dset.id.write_direct_chunk((0, 0), bunchID_data.tobytes(), 0)
        Data_dset.id.write_direct_chunk((0, 0), data_x.tobytes(), 0)
        Data_dset.id.write_direct_chunk((1, 0), data_y.tobytes(), 0)
        file.close()


def read_hdf5_data(self, filename):
    with h5py.File(filename, 'r') as file:
        BunchIDData_dset = file['BunchIDData'][0]
        bunchID_data = BunchIDData_dset[:]

        Data_dset = file['Data']
        data_x = Data_dset[0, :]
        data_y = Data_dset[1, :]

        # print(bunchID_data)
        # print(data_x)
        # print(data_y)
        device_name = file.attrs.get('Device')
        print(device_name)

def startBPMs(self):
    pv_list = get_pv_list()
    #print(pv_list)
    os.environ['EPICS_CA_ADDR_LIST'] = get_addr_list()
    ctx = Context()
    pvs = ctx.get_pvs(*pv_list)
    for pv in pvs:
        pv.wait_for_connection(timeout=10)    #1s
    with Batch() as b:
        for pv in pvs:
             b.write(pv, 0)
             b.write(pv, 1)
    time.sleep(5)
    with Batch() as b:
        for pv in pvs:
             b.write(pv, 0)
    '''
    time.sleep(10)
    with Batch() as b:
        for pv in pvs:
             b.write(pv, 1)
    time.sleep(1)
    with Batch() as b:
        for pv in pvs:
             b.write(pv, 0)
   '''
    #assert set(results) == set(pv.name for pv in pvs)
    #print(results)
