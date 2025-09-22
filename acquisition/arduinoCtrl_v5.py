#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jul 23 10:26:20 2019

@author: bioelectrics
"""
import sys, linecache
from app_logging import get_logger
from multiprocessing import Process
from queue import Empty
import multiCam_DLC_utils_v2 as clara
import time
import serial
        
class arduinoCtrl(Process):
    def __init__(self, ardq, ardq_p2read, frm, com, is_busy, mVal, stim_status, stim_selection, del_style):
        super().__init__()
        self.ardq = ardq
        self.ardq_p2read = ardq_p2read
        self.frm = frm
        self.com = com
        self.is_busy = is_busy
        self.mVal = mVal
        self.stim_status = stim_status
        self.stim_selection = stim_selection
        self.del_style = del_style
        self.pellet_arrived = 0
        self.log = get_logger('arduino')
        
    def run(self):
        serSuccess = False
        user_cfg = clara.read_config()
        try:
            self.ser = serial.Serial('COM'+str(user_cfg['COM']), write_timeout=0.001)
            serSuccess = True
            self.com.value = 0
            self.log.info('Arduino serial connected on COM%s', user_cfg['COM'])
        except:
            exc_type, exc_obj, tb = sys.exc_info()
            f = tb.tb_frame
            lineno = tb.tb_lineno
            filename = f.f_code.co_filename
            linecache.checkcache(filename)
            line = linecache.getline(filename, lineno, f.f_globals)
            self.log.exception('Failed to connect to Arduino (%s line %s): %s', filename, lineno, exc_obj)
            self.log.error('Failed to connect to Arduino')
            self.com.value = -1
            self.ardq_p2read.put('done')
            
        time.sleep(2)
        if serSuccess == True:
            self.log.info('Arduino ready')
            
        self.ardq_p2read.put('done')
        self.record = False 
        while True:
            if not serSuccess:
                self.com.value = -1
                continue
            try:
                if self.is_busy.value == 0:
                    if self.stim_status.value == 2:
                        self.stim_status.value = 0
                        self.com.value = 11
                    if self.com.value > 0:
                        self.comFun()
                if self.ser.in_waiting:
                    line = ''
                    while self.ser.in_waiting:
                        c = self.ser.read()
                        line = line+str(c.strip())[2:-1]
                        if len(line) and line[-1] == '%':
                            self.is_busy.value = 0;
                        
                    if len(line) and line == 'T2000':
                        self.del_style.value = 0
                    elif len(line) and line == 'T2001':
                        self.del_style.value = 1
                    elif len(line) and line[0] == 'T':
                        if self.record:
                            event = line + '_played'
                            self.events.write("%s\t%s\n\r" % (event,self.frm.value))
                    if len(line) and 'HomeFail' in line:
                        self.log.warning('Home Position Fail!')
                        self.is_busy.value = -1
                        time.sleep(5)
                        
                msg = self.ardq.get(block=False)
                try:
                    if msg == 'Release':
                        self.ser.close()
                        self.ardq_p2read.put('done')
                    elif msg == 'recordPrep':
                        path_base = self.ardq.get()
                        self.events = open('%s_events.txt' % path_base, 'w')
                        self.record = True
                        self.ardq_p2read.put('done')
                    elif msg == 'Stop':
                        if self.record:
                            self.events.close()
                            self.record = False
                except:
                    exc_type, exc_obj, tb = sys.exc_info()
                    f = tb.tb_frame
                    lineno = tb.tb_lineno
                    filename = f.f_code.co_filename
                    linecache.checkcache(filename)
                    line = linecache.getline(filename, lineno, f.f_globals)
                    self.log.exception('Exception handling Arduino queue message (%s line %s)', filename, lineno)
                    
                    self.ardq_p2read.put('done')
            
            except Empty:
                pass
    
    def comFun(self):
        stA = time.time()
        comVal = self.com.value
        attmpt = 0
        event = ''
        msg = 'none'
        while True:
            try:
                attmpt+=1
                stB = time.time()
                if comVal == 1:
                    msg = 'H0x'
                elif comVal == 2:
                    msg = 'P0x'
                elif comVal == 3:
                    msg = 'M0x'
                elif comVal == 4:
                    msg = 'R0x'
                    event = 'pellet_delivery'
                    self.log.info('Pellet delivered frame=%s deltaFrames=%s', self.frm.value, round((self.frm.value-self.pellet_arrived)/0.150))
                elif comVal == 5:
                    msg = 'W' + str(self.stim_selection.value) + 'x'
                elif comVal == 6:
                    msg = 'Fx'
                    event = 'pellet_detected'
                    self.pellet_arrived = self.frm.value
                    self.log.info('Pellet detected frame=%s', self.pellet_arrived)
                elif comVal == 7: # block ButtonStyleChange
                    msg = 'D0x'
                elif comVal == 8: # allowButtonStyleChange
                    msg = 'D1x'
                elif comVal == 9: # block ButtonDelivery
                    msg = 'E0x'
                elif comVal == 10: # allowButtonDelivery
                    msg = 'E1x'
                elif comVal == 11:
                    msg = 'Sx'
                    event = 'reach_detected'
                elif comVal == 12:
                    shiftMag = self.mVal.value
                    msg = 'I'+str(5+shiftMag)+'x'
                    print(msg)
                elif comVal == 13:
                    shiftMag = self.mVal.value
                    msg = 'J'+str(25+shiftMag)+'x'
                    print(msg)
                elif comVal == 14:
                    shiftMag = self.mVal.value
                    msg = 'K'+str(shiftMag*(-1)+5)+'x'
                    print(msg)
                elif comVal == 15:
                    msg = 'L'+str(self.del_style.value)+'x'
                    if self.del_style.value == 0:
                        event = 'style_setA'
                        self.log.info('Control style A set')
                    else:
                        event = 'style_setB'
                        self.log.info('Control style B set')
                elif comVal == 16:
                    msg = 'Sx'
                self.ser.write(msg.encode())
                while True:
                    try:
                        if (time.time() > (stB + 0.1)):
                            break
                        elif self.ser.in_waiting:
                            line = ''
                            while self.ser.in_waiting:
                                c = self.ser.read()
                                line = line+str(c.strip())[2:-1]
                                if line[-1] == '!':
                                    break
                            if self.record and len(event):
                                self.events.write("%s\t%s\n\r" % (event,self.frm.value))
                            if len(event):
                                self.log.debug('Recorded event %s frame=%s', event, self.frm.value)
                            self.log.debug('%s in %d attempt(s)', line, attmpt)
                            self.is_busy.value = 1;
                            self.com.value = 0
                            return
                    except:
                        pass
            except:
                exc_type, exc_obj, tb = sys.exc_info()
                f = tb.tb_frame
                lineno = tb.tb_lineno
                filename = f.f_code.co_filename
                linecache.checkcache(filename)
                line = linecache.getline(filename, lineno, f.f_globals)
                print('EXCEPTION IN ({}, LINE {} "{}"): {}'.format(filename, lineno, line.strip(), exc_obj))
                
            
            if (time.time() > (stA + 2)):
                self.log.error('Arduino send fail comVal=%d msg=%s attempts=%d', comVal, msg, attmpt)
                self.com.value = 0
                return



