#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  9 09:21:35 2019

@author: bioelectrics
"""
import subprocess
from multiprocessing import Process
import glob
import os
from pathlib import PurePath
import cv2
import multiCam_DLC_utils_v2 as clara
import shutil
import pathlib, sys as _sys
# Path bootstrap to import root-level app_logging when running from acquisition/
try:
    _ROOT = pathlib.Path(__file__).resolve().parent.parent
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
except Exception:
    pass
from app_logging import get_logger
import json, pathlib, datetime as _dt

class CLARA_compress(Process):
    def __init__(self):
        super().__init__()
        self.log = get_logger('compress')
        
    def run(self):
        try:
            self.log.info('Starting compression process')
            dirlist = list()
            destlist = list()
            user_cfg = clara.read_config()
            read_dir = user_cfg['interim_data_dir']
            write_dir = user_cfg['compressed_data_dir']
            prev_date_list = [name for name in os.listdir(read_dir)]
            for f in prev_date_list:
                unit_dirR = os.path.join(read_dir, f, user_cfg['unitRef'])
                unit_dirW = os.path.join(write_dir, f, user_cfg['unitRef'])
                if os.path.exists(unit_dirR):
                    prev_expt_list = [name for name in os.listdir(unit_dirR)]
                    for s in prev_expt_list:
                        dirlist.append(os.path.join(unit_dirR, s))
                        destlist.append(os.path.join(unit_dirW, s))
                            
            
            for ndx, s in enumerate(dirlist):
                avi_list = os.path.join(s, '*.avi')
                vid_list = glob.glob(avi_list)
                if not os.path.exists(destlist[ndx]):
                    os.makedirs(destlist[ndx])
                if len(vid_list):
                    self.log.info('Compressing %d videos in %s', len(vid_list), s)
                    proc = list()
                    for v in vid_list:
                        vid_name = PurePath(v)
                        dest_path = os.path.join(destlist[ndx], vid_name.stem+'.mp4')
                        passtest = self.testVids(v,str(dest_path))
                        if not passtest:
                            env = os.environ.copy()
                            # Cross-platform ffmpeg discovery:
                            # 1. Respect FFMPEG_DIR or FFMPEG_BIN env var (prepend to PATH)
                            # 2. On Windows, fall back to C:\ffmpeg\bin if it exists
                            ffmpeg_dir = env.get('FFMPEG_DIR') or env.get('FFMPEG_BIN')
                            if not ffmpeg_dir and os.name == 'nt':
                                candidate = r'C:\ffmpeg\bin'
                                if os.path.isdir(candidate):
                                    ffmpeg_dir = candidate
                            if ffmpeg_dir and os.path.isdir(ffmpeg_dir):
                                env["PATH"] = ffmpeg_dir + os.pathsep + env["PATH"]
                            # Build command list (avoid shell quoting issues)
                            command = [
                                'ffmpeg', '-y', '-i', v,
                                '-c:v', 'libx264', '-preset', 'veryfast',
                                '-vf', 'format=yuv420p', '-c:a', 'copy',
                                '-crf', '17', '-loglevel', 'quiet', str(dest_path)
                            ]
                            proc.append(subprocess.Popen(command, env=env, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE))

                    for p in proc:
                        p.wait()
                    passvals = list()
                    for v in vid_list:
                        vid_name = PurePath(v)
                        dest_path = os.path.join(destlist[ndx], vid_name.stem+'.mp4')
                        passval = self.testVids(v,str(dest_path))
                        passvals.append(passval)
                        if passval:
                            os.remove(v)
                            self.log.info('Successfully compressed %s', vid_name.stem)
                        else:
                            self.log.error('Error compressing %s', vid_name.stem)
                metafiles = glob.glob(os.path.join(s,'*'))
                for m in metafiles:
                    mname = PurePath(m).name
                    mdest = os.path.join(destlist[ndx],mname)
                    if not os.path.isfile(mdest):
                        if not '.avi' in m:
                            shutil.copyfile(m,mdest)
            # After video compression, process metalink entries
            try:
                self._process_metalink(user_cfg)
            except Exception:
                self.log.exception('Failed processing metalink entries')
            self.log.info('Compression is complete')
        except Exception as ex:
            self.log.exception('Compression process failed: %s', ex)
            
    def testVids(self, v, dest_path):
        try:
            vid = cv2.VideoCapture(v)
            numberFramesA = int(vid.get(cv2.CAP_PROP_FRAME_COUNT))
            vid = cv2.VideoCapture(str(dest_path))
            numberFramesB = int(vid.get(cv2.CAP_PROP_FRAME_COUNT))
            if (numberFramesA == numberFramesB) and (numberFramesA > 0):
                passval = True
            else:
                passval = False
        except:
            passval = False
            
        return passval

    def _process_metalink(self, user_cfg):
        """Read temp/metalink.txt and materialize metadata into destination session folders.

        For each line JSON object with keys rfid, session_dir, session_name, raw_meta, mouse.
        We derive compressed session path from interim directory structure and create
        a metadata file: <date>_<unitRef>_<session>_rfid_<rfid>_mousemeta.json
        """
        tmp_dir = pathlib.Path(__file__).parent / 'temp'
        metalink_path = tmp_dir / 'metalink.txt'
        if not metalink_path.exists():
            self.log.info('No metalink file found, skipping metadata link step')
            return
        lines = metalink_path.read_text(encoding='utf-8').strip().splitlines()
        if not lines:
            self.log.info('Metalink file empty')
            return
        remaining = []
        processed = 0
        for ln in lines:
            try:
                obj = json.loads(ln)
                session_dir = obj.get('session_dir')
                rfid = obj.get('rfid')
                if not (session_dir and rfid):
                    self.log.warning('Skipping malformed metalink entry: %s', ln)
                    continue
                # Build compressed destination path: replace interim_data_dir raw root with compressed root
                # session_dir layout: raw_data_dir/YYYYMMDD/unitRef/sessionXYZ
                # compressed path we produced: compressed_data_dir/YYYYMMDD/unitRef/sessionXYZ
                # Retrieve date/unitRef from session_dir
                try:
                    parts = pathlib.Path(session_dir).parts
                    # find last 4 path parts containing date, unitRef, session name
                    session_name = obj.get('session_name') or parts[-1]
                    unitRef = user_cfg['unitRef']
                    # search for date pattern YYYYMMDD in parts
                    date_part = None
                    for p in parts:
                        if len(p) == 8 and p.isdigit():
                            date_part = p
                    if not date_part:
                        raise ValueError('date segment not found in session_dir')
                    compressed_session = pathlib.Path(user_cfg['compressed_data_dir']) / date_part / unitRef / session_name
                    compressed_session.mkdir(parents=True, exist_ok=True)
                    meta_filename = f"{date_part}_{unitRef}_{session_name}_rfid_{rfid}_mousemeta.json"
                    meta_dest = compressed_session / meta_filename
                    payload = {
                        'rfid': rfid,
                        'session_name': session_name,
                        'timestamp_linked': _dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
                        'mouse': obj.get('mouse'),
                        'raw_meta_source': obj.get('raw_meta')
                    }
                    meta_dest.write_text(json.dumps(payload, indent=2), encoding='utf-8')
                    self.log.info('Metalink metadata written %s', meta_dest)
                    processed += 1
                except Exception as e:
                    self.log.warning('Failed to process metalink entry for session_dir=%s: %s', session_dir, e)
                    remaining.append(ln)
            except json.JSONDecodeError:
                self.log.warning('Invalid JSON in metalink: %s', ln)
        # Rewrite remaining entries (those not processed) to metalink
        if remaining:
            metalink_path.write_text('\n'.join(remaining) + '\n', encoding='utf-8')
            self.log.info('Metalink processed=%d remaining=%d', processed, len(remaining))
        else:
            metalink_path.unlink(missing_ok=True)
            self.log.info('All metalink entries processed=%d file removed', processed)
