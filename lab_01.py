#!/usr/bin/env python
#============================================
#
# Это мой первый скрипт на python. Правда.
# Но есть опыт разработки на TCL
# Не смотрел как делают коллеги, но думаю отличительной особенностью
# этого скрипта будет использование SNMP
#
# Спасибо!
#
#============================================

#Imports
from netmiko import ConnectHandler
from netmiko import NetMikoAuthenticationException, NetMikoTimeoutException
from pysnmp.hlapi import *
import csv
import datetime
import multiprocessing as mp
import sys
import os
import re
import time

#Module 'Global' variables
DEVICE_FILE_PATH = 'devices.csv' # file should contain a list of devices in format: ip,username,password,device_type
BACKUP_DIR_PATH = 'Backups' # complete path to backup directory
COMM = 'publ'
NTP = '172.18.65.11'

def get_devices_from_file(device_file):
    # Это заимствовано

    # creating empty structures
    device_list = list()
    device = dict()

    # reading a CSV file with ',' as a delimeter
    with open(device_file, 'r') as f:
        reader = csv.DictReader(f, delimiter=';')

        # every device represented by single row which is a dictionary object with keys equal to column names.
        for row in reader:
            device_list.append(row)

    print ("Got the device list from inventory\n")
    # returning a list of dictionaries
    return device_list

def get_current_date_and_time():
    # Это заимствовано

    now = datetime.datetime.now()

    print("Got a timestamp\n")
    
    # Returning a formatted date string
    # Format: yyyy_mm_dd-hh_mm_ss
    return now.strftime("%Y_%m_%d-%H_%M_%S")

def connect_to_device(device):
    # Это заимствовано

    connection = ConnectHandler(
        host = device['ip'],
        username = device['username'],
        password=device['password'],
        device_type=device['device_type'],
        secret=device['secret']
    )
    # returns a "connection" object
    return connection

def disconnect_from_device(connection, hostname):
    # Это заимствовано

    connection.disconnect()

def get_backup_file_path(hostname,timestamp):
    # Это заимствовано

    if not os.path.exists(os.path.join(BACKUP_DIR_PATH, hostname)):
        os.mkdir(os.path.join(BACKUP_DIR_PATH, hostname))

    # Merging a string to form a full backup file name
    backup_file_path = os.path.join(BACKUP_DIR_PATH, hostname, '{}-{}.txt'.format(hostname, timestamp))
    # returning backup file path
    return backup_file_path

def create_backup(connection, backup_file_path, hostname):
    # This function pulls running configuration from a device and writes it to the backup file
    # Requires connection object, backup file path and a device hostname as an input

    try:
        # sending a CLI command using Netmiko and printing an output
        connection.enable()
        output = connection.send_command('sh run')

        # creating a backup file and writing command output to it
        with open(backup_file_path, 'w') as file:
            file.write(output)
        # if successfully done
        return True

    except Error:
        # if there was an error
        return False
    
def ntp_chk(connection):
    try:
        # конфигурируем  timezone
        connection.send_config_set('clock timezone MSK 3 0')
        # Проверяем конфиг времени, если ntp сервер не сконфигурирован, то добавляем
        if not re.search(NTP,connection.send_command('sh ntp config | i'+NTP)): 
            # Если нужного ntp нет, проверяем его пингом по 1 пакету до 5 раз, так быстрее в случае его доступности
            i = 0
            ok = 0
            cmd = 'ping '+NTP+' rep 1'
            while i < 5 and not ok:
                i += 1
                ok = re.search('Success rate is 100 percent', connection.send_command(cmd))
            if ok:
                cmd = 'ntp server '+NTP+' prefer'
                connection.send_config_set(cmd)
        i = 0
        # Проверяем синхронизацию до 6 раз через 10 секунд
        while i < 6: 
            i += 1
            if re.search('Clock is synchronized',connection.send_command('sh ntp status | i Clock')): return True
            time.sleep(10)
        return False
    except Error:
        return False
                        
def snmp_get_exact(community, ip, oid):
    # Чтение заданного oid
    errorIndication, errorStatus, errorIndex, varBinds = next(
        getCmd(SnmpEngine(),
        CommunityData(community),
        UdpTransportTarget((ip, 161)),
        ContextData(),
        ObjectType(ObjectIdentity(ObjectIdentity(oid))))
    )
    if errorIndication or errorStatus:
         return ('Error', False)
    else:
        oid, value = varBinds[0]
        return (str(oid), value.prettyPrint(), True)        
        
def snmp_get_next(community, ip, oid):
    # Чтение oid следующего за текущим
    errorIndication, errorStatus, errorIndex, varBinds = next(
        nextCmd(SnmpEngine(),
        CommunityData(community),
        UdpTransportTarget((ip, 161)),
        ContextData(),
        ObjectType(ObjectIdentity(ObjectIdentity(oid))))
    )
    if errorIndication or errorStatus:
        return ('Error', 'Error', False)
    else:
        oid, value = varBinds[0]
        return (str(oid), value.prettyPrint(), True)        
    
def process_target(device,timestamp):
    #  - connects to the device,
    #  - gets a backup file name and a hostname for this device,
    #  - creates a backup for this device
    #  - устанавливает/проверяет синхронизацию времени с ntp                       
    #  - terminates connection
    #  - По snmp получает всю остальную необходимую информацию
    # Requires connection object and a timestamp string as an input

    
    connection = connect_to_device(device)
    
    backup_file_path = get_backup_file_path(device['hostname'], timestamp)
    backup_result = create_backup(connection, backup_file_path, device['hostname'])
    ntp_stat = 'Clock not in Sync'
    bakup = 'Config not backuped'
    if backup_result: 
        #bakup (ssh) успешен
        bakup = 'Config backuped'
        if ntp_chk(connection): ntp_stat = 'Clock in Sync'
 
    disconnect_from_device(connection, device['hostname'])
    report = device['hostname']
    # Читаем entPhysicalModelName
    oid, model, flag = (snmp_get_next(COMM, device['ip'], '1.3.6.1.2.1.47.1.1.1.1.13'))
    # В составных устройствах может быть пусто в .1, поэтому ищем первый заполненный oid... это большое упрощение, по хорошему надо разбирать по устройствам
    while model == '' and re.search('1.3.6.1.2.1.47.1.1.1.1.13', oid):
        oid, model, flag = (snmp_get_next(COMM, device['ip'], oid))
        if not flag: break
    if flag:
        # По SNMP подключились успешно
        report = report +'|'+model        
        # Читаем sysConfigName
        oid, ios, flag = (snmp_get_exact(COMM, device['ip'], '1.3.6.1.4.1.9.2.1.73.0'))   
        # Выделяем имя IOS
        ios = ios.split('/')[-1]
        ios = ios.split(':')[-1]
        report = report +'|'+ios
        # NPE/PE
        if re.search('npe', ios): ios_type = 'NPE'
        else: ios_type = 'PE '
        report = report +'|'+ios_type
        # читаем cdpGlobal    
        oid, value, flag = (snmp_get_exact(COMM, device['ip'], '1.3.6.1.4.1.9.9.23.1.3.1.0'))
        if value == '1':
            # CDP включен
            report = report +'|CDP is ON '
            # Далее считываем количество соседей по всем интерфейсам и суммируем их
            oid = '1.3.6.1.4.1.9.9.23.1.2.1.1.3'
            neib = 0
            oid, value, flag = (snmp_get_next(COMM, device['ip'], oid))
            while re.search('23.1.2.1.1.3', oid):
                neib = neib + int(value)
                oid, value, flag = (snmp_get_next(COMM, device['ip'], oid))
                if not flag: break
            report = report+','+str(neib)+' peers'
        else:
            # CDP выключен
            report = report +'|CDP is OFF'
    else:
        # Ошибка SNMP
        report = report +'|SNMP Error'
    report = report +'|'+ntp_stat+'|'+bakup
    print(report)

def main(*args):
    # This is a main function

    # getting the timestamp string
    timestamp = get_current_date_and_time()
    print (timestamp)

    # getting a device list from the file in a python format
    device_list = get_devices_from_file(DEVICE_FILE_PATH)

    # creating a empty list
    processes=list()

    # Running workers to manage connections
    with mp.Pool(4) as pool:
        # Starting several processes...
        for device in device_list:
            processes.append(pool.apply_async(process_target, args=(device,timestamp)))
        # Waiting for results...
        for process in processes:
            process.get()


if __name__ == '__main__':
    # checking if we run independently
    _, *script_args = sys.argv
    
    # the execution starts here
    main(*script_args)






