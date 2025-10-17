"""
CLARA toolbox
https://github.com/wryanw/CLARA
W Williamson, wallace.williamson@ucdenver.edu

"""


from __future__ import print_function
from multiprocessing import Array, Queue, Value
import multiprocessing as mp  # for spawning background processes (RFID, export) outside existing imports
import wx
import wx.lib.dialogs
import os
import numpy as np
import time, datetime
import ctypes
from matplotlib.figure import Figure
import matplotlib.patches as patches
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
import multiCam_DLC_PySpin_v2 as spin
import multiCam_DLC_utils_v2 as clara
import arduinoCtrl_v5 as arduino
import compressVideos_v3 as compressVideos
import shutil
from pathlib import Path
import ruamel.yaml
import winsound
import queue

# --- Path bootstrap to allow running this script from nested acquisition/ folder ---
import pathlib, sys as _sys
try:
    _ROOT = pathlib.Path(__file__).resolve().parent.parent
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
except Exception:
    pass

from app_logging import get_logger

# ###########################################################################
# Class for GUI MainFrame
# ###########################################################################
class ImagePanel(wx.Panel):

    def __init__(self, parent, gui_size, axesCt, **kwargs):
        wx.Panel.__init__(self, parent, -1,style=wx.SUNKEN_BORDER)
            
        self.figure = Figure()
        self.axes = list()
        if axesCt <= 3:
            if gui_size[0] > gui_size[1]:
                rowCt = 1
                colCt = axesCt
            else:
                colCt = 1
                rowCt = axesCt
            
        else:
            if gui_size[0] > gui_size[1]:
                rowCt = 2
                colCt = np.ceil(axesCt/2)
            else:
                colCt = 2
                rowCt = np.ceil(axesCt/2)
        a = 0
        for r in range(int(rowCt)):
            for c in range(int(colCt)):
                self.axes.append(self.figure.add_subplot(rowCt, colCt, a+1, frameon=True))
                self.axes[a].set_position([c*1/colCt+0.005,r*1/rowCt+0.005,1/colCt-0.01,1/rowCt-0.01])
                
        
                self.axes[a].xaxis.set_visible(False)
                self.axes[a].yaxis.set_visible(False)
                a+=1
            
        self.canvas = FigureCanvas(self, -1, self.figure)
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.sizer.Add(self.canvas, 1, wx.LEFT | wx.TOP | wx.GROW)
        self.SetSizer(self.sizer)
        self.Fit()

    def getfigure(self):
        """
        Returns the figure, axes and canvas
        """
        return(self.figure,self.axes,self.canvas)
#    
class WidgetPanel(wx.Panel):
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, -1,style=wx.SUNKEN_BORDER)

class MainFrame(wx.Frame):
    """Contains the main GUI and button boxes.

    Parameters
    ----------
    simulate : bool
        Start directly in simulation (no hardware) mode; useful with --simulate flag.
    """
    def __init__(self, parent, simulate: bool=False):
        
# Settting the GUI size and panels design
        displays = (wx.Display(i) for i in range(wx.Display.GetCount())) # Gets the number of displays
        screenSizes = [display.GetGeometry().GetSize() for display in displays] # Gets the size of each display
        index = 0 # For display 1.
        screenW = screenSizes[index][0]
        screenH = screenSizes[index][1]
        
        self.system_cfg = clara.read_config()
        key_list = list()
        for cat in self.system_cfg.keys():
            key_list.append(cat)
        self.camStrList = list()
        for key in key_list:
            if 'cam' in key:
                self.camStrList.append(key)
        self.slist = list()
        self.mlist = list()
        for s in self.camStrList:
            if not self.system_cfg[s]['ismaster']:
                self.slist.append(str(self.system_cfg[s]['serial']))
            else:
                self.mlist.append(str(self.system_cfg[s]['serial']))
        
        self.camCt = len(self.camStrList)
        
        self.gui_size = (800,1750)
        if screenW > screenH:
            self.gui_size = (1750,650)
        wx.Frame.__init__ ( self, parent, id = wx.ID_ANY, title = 'RT Video Acquisition',
                            size = wx.Size(self.gui_size), pos = wx.DefaultPosition, style = wx.RESIZE_BORDER|wx.DEFAULT_FRAME_STYLE|wx.TAB_TRAVERSAL )
        self.statusbar = self.CreateStatusBar()
        self.statusbar.SetStatusText("")
        self.log = get_logger('gui')
        self.log.info('GUI MainFrame initialized')
        # Simulation mode: set True when camera hardware init fails so GUI can still be exercised
        self.simulate_mode = bool(simulate)
        # Collect initialization / hardware errors for later user inspection
        self.init_errors = []

        self.SetSizeHints(wx.Size(self.gui_size)) #  This sets the minimum size of the GUI. It can scale now!
        
###################################################################################################################################################
# Spliting the frame into top and bottom panels. Bottom panels contains the widgets. The top panel is for showing images and plotting!
        self.guiDim = 0
        if screenH > screenW:
            self.guiDim = 1
        topSplitter = wx.SplitterWindow(self)
        self.image_panel = ImagePanel(topSplitter,self.gui_size, self.camCt)
        self.widget_panel = WidgetPanel(topSplitter)
        if self.guiDim == 0:
            topSplitter.SplitVertically(self.image_panel, self.widget_panel,sashPosition=int(self.gui_size[0]*0.75))
        else:
            topSplitter.SplitHorizontally(self.image_panel, self.widget_panel,sashPosition=int(self.gui_size[1]*0.75))
        topSplitter.SetSashGravity(0.5)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(topSplitter, 1, wx.EXPAND)
        self.SetSizer(sizer)

###################################################################################################################################################
# Add Buttons to the WidgetPanel and bind them to their respective functions.
        
        

        wSpace = 0
        wSpacer = wx.GridBagSizer(5, 5)
        
        camctrlbox = wx.StaticBox(self.widget_panel, label="Camera Control")
        bsizer = wx.StaticBoxSizer(camctrlbox, wx.HORIZONTAL)
        camsizer = wx.GridBagSizer(5, 5)
        
        bw = 76
        vpos = 0
        
        self.init = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Initialize", size=(bw,-1))
        camsizer.Add(self.init, pos=(vpos,0), span=(1,3), flag=wx.ALL, border=wSpace)
        self.init.Bind(wx.EVT_TOGGLEBUTTON, self.initCams)
        
        self.crop = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label="Crop")
        camsizer.Add(self.crop, pos=(vpos,3), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.crop.SetValue(1)
        
        self.update_settings = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Update Settings", size=(bw*2, -1))
        camsizer.Add(self.update_settings, pos=(vpos,6), span=(1,6), flag=wx.ALL, border=wSpace)
        self.update_settings.Bind(wx.EVT_BUTTON, self.updateSettings)
        self.update_settings.Enable(False)
        
        vpos+=1
        self.set_pellet_pos = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Pellet", size=(bw, -1))
        camsizer.Add(self.set_pellet_pos, pos=(vpos,0), span=(0,3), flag=wx.TOP | wx.BOTTOM, border=3)
        self.set_pellet_pos.Bind(wx.EVT_TOGGLEBUTTON, self.setCrop)
        self.set_pellet_pos.Enable(False)
        
        
        self.set_roi = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Hand ROI", size=(bw, -1))
        camsizer.Add(self.set_roi, pos=(vpos,3), span=(0,3), flag=wx.TOP, border=0)
        self.set_roi.Bind(wx.EVT_TOGGLEBUTTON, self.setCrop)
        self.set_roi.Enable(False)
        
        self.set_crop = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Set Crop ROI", size=(bw*2, -1))
        camsizer.Add(self.set_crop, pos=(vpos,6), span=(0,6), flag=wx.TOP, border=0)
        self.set_crop.Bind(wx.EVT_TOGGLEBUTTON, self.setCrop)
        self.set_crop.Enable(False)
        
        vpos+=1
        self.play = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Live", size=(bw, -1))
        camsizer.Add(self.play, pos=(vpos,0), span=(1,3), flag=wx.ALL, border=wSpace)
        self.play.Bind(wx.EVT_TOGGLEBUTTON, self.liveFeed)
        self.play.Enable(False)
        
        self.rec = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Record", size=(bw, -1))
        camsizer.Add(self.rec, pos=(vpos,3), span=(1,3), flag=wx.ALL, border=wSpace)
        self.rec.Bind(wx.EVT_TOGGLEBUTTON, self.recordCam)
        self.rec.Enable(False)
        
        self.minRec = wx.SpinCtrl(self.widget_panel, value='20', size=(50, -1))
        self.minRec.Enable(False)
        min_text = wx.StaticText(self.widget_panel, label='M:')
        camsizer.Add(self.minRec, pos=(vpos,7), span=(1,2), flag=wx.ALL, border=wSpace)
        camsizer.Add(min_text, pos=(vpos,6), span=(1,1), flag=wx.TOP, border=5)
        self.minRec.SetMax(300)
        
        self.set_stim = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label="Stim ROI", size=(bw, -1))
        camsizer.Add(self.set_stim, pos=(vpos,9), span=(0,3), flag=wx.TOP, border=0)
        self.set_stim.Bind(wx.EVT_TOGGLEBUTTON, self.setCrop)
        self.set_stim.Enable(False)
        
        camsize = 5
        vpos+=camsize
        bsizer.Add(camsizer, 1, wx.EXPAND | wx.ALL, 5)
        wSpacer.Add(bsizer, pos=(0, 0), span=(camsize,3),flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=wSpace)
        # wSpacer.Add(bsizer, pos=(0, 0), span=(vpos,3),flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=5)

        serctrlbox = wx.StaticBox(self.widget_panel, label="Serial Control")
        sbsizer = wx.StaticBoxSizer(serctrlbox, wx.HORIZONTAL)
        sersizer = wx.GridBagSizer(5, 5)
        
        vpos = 0
        
        self.send_home = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Home", size=(bw, -1))
        sersizer.Add(self.send_home, pos=(vpos,0), span=(0,3), flag=wx.LEFT, border=wSpace)
        self.send_home.Bind(wx.EVT_BUTTON, self.comFun)
        
        self.load_pellet = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Pellet", size=(bw, -1))
        sersizer.Add(self.load_pellet, pos=(vpos,3), span=(0,3), flag=wx.LEFT, border=wSpace)
        self.load_pellet.Bind(wx.EVT_BUTTON, self.comFun)
        
        self.send_pellet = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Mouse", size=(bw, -1))
        sersizer.Add(self.send_pellet, pos=(vpos,6), span=(0,3), flag=wx.LEFT, border=wSpace)
        self.send_pellet.Bind(wx.EVT_BUTTON, self.comFun)
        
        self.trig_release = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Release", size=(bw, -1))
        sersizer.Add(self.trig_release, pos=(vpos,9), span=(0,3), flag=wx.LEFT, border=wSpace)
        self.trig_release.Bind(wx.EVT_BUTTON, self.comFun)
        
        vpos+=1
        
        self.Xmag = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='X (mm):')
        sersizer.Add(min_text, pos=(vpos,0), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.Xmag, pos=(vpos,3), span=(1,3), flag=wx.ALL, border=wSpace)
        self.Xmag.SetMax(5)
        self.Xmag.SetMin(-5)
        self.Xmag.Bind(wx.EVT_SPINCTRL, self.comFun)
        
        self.Ymag = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='Y (mm):')
        sersizer.Add(min_text, pos=(vpos,6), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.Ymag, pos=(vpos,9), span=(1,3), flag=wx.ALL, border=wSpace)
        self.Ymag.SetMax(5)
        self.Ymag.SetMin(-5)
        self.Ymag.Bind(wx.EVT_SPINCTRL, self.comFun)
        
        vpos+=1
        
        self.Zmag = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='Z (mm):')
        sersizer.Add(min_text, pos=(vpos,0), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.Zmag, pos=(vpos,3), span=(1,3), flag=wx.ALL, border=wSpace)
        self.Zmag.SetMax(5)
        self.Zmag.SetMin(-5)
        self.Zmag.Bind(wx.EVT_SPINCTRL, self.comFun)
        
        self.send_stim = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Send stim", size=(bw, -1))
        sersizer.Add(self.send_stim, pos=(vpos,6), span=(1,3), flag=wx.LEFT, border=wSpace)
        self.send_stim.Bind(wx.EVT_BUTTON, self.comFun)
        
        self.toggle_style = wx.Button(self.widget_panel, id=wx.ID_ANY, label=" ", size=(bw, -1))
        sersizer.Add(self.toggle_style, pos=(vpos,9), span=(1,3), flag=wx.LEFT, border=wSpace)
        self.toggle_style.Bind(wx.EVT_BUTTON, self.toggleStyle)
        
        vpos+=1
        
        self.tone_delay_min = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='Wait Min (ms):')
        sersizer.Add(min_text, pos=(vpos,0), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.tone_delay_min, pos=(vpos,3), span=(1,3), flag=wx.ALL, border=wSpace)
        self.tone_delay_min.SetMax(20000)
        self.tone_delay_min.SetMin(0)
        self.tone_delay_min.Bind(wx.EVT_SPINCTRL, self.comFun)
        
        self.tone_delay_max = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='Wait Max (ms):')
        sersizer.Add(min_text, pos=(vpos,6), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.tone_delay_max, pos=(vpos,9), span=(1,3), flag=wx.ALL, border=wSpace)
        self.tone_delay_max.SetMax(20000)
        self.tone_delay_max.SetMin(0)
        self.tone_delay_max.Bind(wx.EVT_SPINCTRL, self.comFun)
        
        vpos+=1
        
        self.delay_count = wx.SpinCtrl(self.widget_panel, value=str(0), size=(bw, -1))
        min_text = wx.StaticText(self.widget_panel, label='Interval #:')
        sersizer.Add(min_text, pos=(vpos,0), span=(1,3), flag=wx.TOP, border=wSpace)
        sersizer.Add(self.delay_count, pos=(vpos,3), span=(1,3), flag=wx.ALL, border=wSpace)
        self.delay_count.SetMax(50)
        self.delay_count.SetMin(1)
        self.delay_count.Bind(wx.EVT_SPINCTRL, self.comFun)

        self.auto_delay = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label="Delay pellet reveal", size=(bw*2, -1))
        sersizer.Add(self.auto_delay, pos=(vpos,6), span=(0,6), flag=wx.LEFT, border=wSpace)
        self.auto_delay.SetValue(0)
        self.auto_delay.Bind(wx.EVT_CHECKBOX, self.comFun)

        sersize = vpos
        vpos = camsize
        sbsizer.Add(sersizer, 1, wx.EXPAND | wx.ALL, 5)
        wSpacer.Add(sbsizer, pos=(vpos, 0), span=(sersize,3),flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT, border=wSpace)
        self.serHlist = [self.send_home, self.auto_delay, self.load_pellet,
                          self.trig_release, self.send_pellet, self.tone_delay_min,
                          self.delay_count, self.tone_delay_max, self.send_stim,
                          self.Xmag,self.Ymag,self.Zmag,self.toggle_style]
        for h in self.serHlist:
            h.Enable(False)
        
        wSpace = 10
        vpos+=sersize
        
        self.slider = wx.Slider(self.widget_panel, -1, 0, 0, 100,size=(300, -1), style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_LABELS )
        wSpacer.Add(self.slider, pos=(vpos,0), span=(0,3), flag=wx.LEFT, border=wSpace)
        self.slider.Enable(False)
        
        vpos+=1
        
        start_text = wx.StaticText(self.widget_panel, label='Select user:')
        wSpacer.Add(start_text, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.user_drop = wx.Choice(self.widget_panel, size=(100, -1), id=wx.ID_ANY, choices=[' '])
        wSpacer.Add(self.user_drop, pos=(vpos,1), span=(0,1), flag=wx.ALL, border=wSpace)
        self.user_drop.Bind(wx.EVT_CHOICE, self.selectUser)
        
        self.add_user = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Add User")
        wSpacer.Add(self.add_user, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.add_user.Bind(wx.EVT_BUTTON, self.addUser)
        
        vpos+=1

        # RFID entry & lookup (local SoftMouse mirror)
        rfid_label = wx.StaticText(self.widget_panel, label='RFID:')
        wSpacer.Add(rfid_label, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.rfid_input = wx.TextCtrl(self.widget_panel, id=wx.ID_ANY, value="")
        wSpacer.Add(self.rfid_input, pos=(vpos,1), span=(0,1), flag=wx.ALL, border=wSpace)
        self.rfid_lookup = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Lookup RFID")
        wSpacer.Add(self.rfid_lookup, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.rfid_lookup.Bind(wx.EVT_BUTTON, self.lookupRFID)
        vpos+=1

        # Animals status (SoftMouse configuration moved to Config->SoftMouse Config)
        self.softmouse_status = wx.StaticText(self.widget_panel, label='Animals: none loaded')
        wSpacer.Add(self.softmouse_status, pos=(vpos,0), span=(0,3), flag=wx.LEFT, border=wSpace)
        vpos+=1

        # RFID listener controls moved to Config->RFID Config dialog. Provide minimal status inline.
        self.rfid_status = wx.StaticText(self.widget_panel, label='RFID idle')
        wSpacer.Add(self.rfid_status, pos=(vpos,0), span=(0,3), flag=wx.LEFT, border=wSpace)
        vpos+=1
        # RFID toggle (always autostarts on launch, but allows user to stop/restart)
        self.rfid_toggle = wx.ToggleButton(self.widget_panel, id=wx.ID_ANY, label='RFID On/Off')
        wSpacer.Add(self.rfid_toggle, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.rfid_toggle.Bind(wx.EVT_TOGGLEBUTTON, self._on_rfid_toggle)
        vpos+=1

        start_text = wx.StaticText(self.widget_panel, label='Stim on:')
        wSpacer.Add(start_text, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        protocol_list = ['First Reach','Pellet Arrival','Pellet Reveal']
        self.protocol = wx.Choice(self.widget_panel, size=(100, -1), id=wx.ID_ANY, choices=protocol_list)
        wSpacer.Add(self.protocol, pos=(vpos,1), span=(0,1), flag=wx.ALL, border=wSpace)
        self.protocol.SetSelection(1)
        self.protocol.Bind(wx.EVT_CHOICE, self.setProtocol)
        
        self.expt_id = wx.TextCtrl(self.widget_panel, id=wx.ID_ANY, value="SessionRef")
        wSpacer.Add(self.expt_id, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        
        vpos+=1
        start_text = wx.StaticText(self.widget_panel, label='Automate:')
        wSpacer.Add(start_text, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        
        self.auto_pellet = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label="Pellet")
        wSpacer.Add(self.auto_pellet, pos=(vpos,1), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.auto_pellet.SetValue(0)
        self.auto_pellet.Bind(wx.EVT_CHECKBOX, self.autoPellet)
        self.auto_pellet.Enable(False)
        
        self.auto_stim = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label="Stimulus")
        wSpacer.Add(self.auto_stim, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.auto_stim.SetValue(0)
        
        vpos+=1
        start_text = wx.StaticText(self.widget_panel, label='Inspect values within ROIs:')
        wSpacer.Add(start_text, pos=(vpos,0), span=(0,3), flag=wx.LEFT, border=wSpace)
        # Add three inspection toggles (pellet intensity, hand ROI, stim ROI)
        vpos+=1
        self.inspect_pellet = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label='Pellet')
        wSpacer.Add(self.inspect_pellet, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.inspect_hand = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label='Hand')
        wSpacer.Add(self.inspect_hand, pos=(vpos,1), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.inspect_stim = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label='Stim')
        wSpacer.Add(self.inspect_stim, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        # Stim inspection disabled until init reveals a stim camera (enabled later)
        self.inspect_stim.Enable(False)
        
        # --- New: Save to Database checkbox ---
        vpos+=1
        self.save_to_db = wx.CheckBox(self.widget_panel, id=wx.ID_ANY, label="Save to DB (sessions/live)")
        wSpacer.Add(self.save_to_db, pos=(vpos,0), span=(0,2), flag=wx.LEFT, border=wSpace)
        self.save_to_db.SetValue(True)
        self.save_to_db.Bind(wx.EVT_CHECKBOX, lambda evt: None)
        
        vpos+=1

        # Compress videos button
        self.compress_vid = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Compress Vids")
        wSpacer.Add(self.compress_vid, pos=(vpos,0), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.compress_vid.Bind(wx.EVT_BUTTON, self.compressVid)
        # self.compress_vid.Enable(False)

        self.quit = wx.Button(self.widget_panel, id=wx.ID_ANY, label="Quit")
        wSpacer.Add(self.quit, pos=(vpos,2), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.quit.Bind(wx.EVT_BUTTON, self.quitButton)
        self.Bind(wx.EVT_CLOSE, self.quitButton)

        # Hardware status / help button (added near Quit/compress)
        vpos_help = vpos  # row index for HW Status
        self.hw_status_btn = wx.Button(self.widget_panel, id=wx.ID_ANY, label="HW Status")
        wSpacer.Add(self.hw_status_btn, pos=(vpos_help,1), span=(0,1), flag=wx.LEFT, border=wSpace)
        self.hw_status_btn.Bind(wx.EVT_BUTTON, self.showHardwareStatus)

        self.widget_panel.SetSizer(wSpacer)
        wSpacer.Fit(self.widget_panel)
        self.widget_panel.Layout()
        
        self.disable4cam = [self.minRec, self.update_settings,
                            self.expt_id, self.set_pellet_pos, self.set_roi]
        
        self.onWhenCamEnabled = [self.play, self.rec, self.minRec,
                                 self.update_settings, self.set_pellet_pos, self.set_roi]

        self.liveTimer = wx.Timer(self, wx.ID_ANY)
        self.recTimer = wx.Timer(self, wx.ID_ANY)
        
        self.figure,self.axes,self.canvas = self.image_panel.getfigure()
        self.figure.canvas.draw()
        # If simulation requested immediately, draw overlay now
        if self.simulate_mode:
            self._add_simulation_overlays(initial=True)

        self.pellet_x = self.system_cfg['pelletXY'][0]
        self.pellet_y = self.system_cfg['pelletXY'][1]
        
        self.is_busy = Value(ctypes.c_byte, 0)
        self.roi = np.asarray(self.system_cfg['roiXWYH'], int)
        self.stimroi = np.asarray(self.system_cfg['stimXWYH'], int)
        self.failCt = 0
        
        self.currAxis = 0
        self.x1 = 0
        self.y1 = 0
        self.im = list()
        self.proto_str = 'none'
    # Mouse metadata cache (populated by RFID lookup)
        self.mouse_meta = dict()
        
        self.figure,self.axes,self.canvas = self.image_panel.getfigure()
        
        self.im = list()
        self.delivery_delay = time.time()
        self.frmDims = [0,270,0,360]
        self.camIDlsit = list()
        self.dlc = Value(ctypes.c_byte, 0)
        self.stim_status = Value(ctypes.c_byte, 0)
        self.camaq = Value(ctypes.c_byte, 0)
        self.frmaq = Value(ctypes.c_int, 0)
        self.com = Value(ctypes.c_int, -1)
        self.mVal = Value(ctypes.c_int, 0)
        self.stim_selection = Value(ctypes.c_int, 0)
        self.del_style = Value(ctypes.c_int, 0)
        self.pellet_timing = time.time()
        self.pellet_status = 3
        self.pLoc = list()
        self.croprec = list()
        self.croproi = list()
        self.frame = list()
        self.frameBuff = list()
        self.dtype = 'uint8'
        self.frmGrab = list()
        self.size = self.frmDims[1]*self.frmDims[3]
        self.shape = [self.frmDims[1], self.frmDims[3]]
        frame = np.zeros(self.shape, dtype='ubyte')
        frameBuff = np.zeros(self.size, dtype='ubyte')
        self.markerSize = 6
        self.cropPts = list()    
        self.array4feed = list()
        self.roirec = list()
        self.stimrec = list()
        self.stimAxes = None
        for ndx, s in enumerate(self.camStrList):
            self.camIDlsit.append(str(self.system_cfg[s]['serial']))
            self.croproi.append(self.system_cfg[s]['crop'])
            self.array4feed.append(Array(ctypes.c_ubyte, self.size))
            self.frmGrab.append(Value(ctypes.c_byte, 0))
            self.frame.append(frame)
            self.frameBuff.append(frameBuff)
            self.im.append(self.axes[ndx].imshow(self.frame[ndx],cmap='gray'))
            self.im[ndx].set_clim(0,255)
            self.points = [-10,-10,1.0]
            
            circle = [patches.Circle((-10, -10), radius=5, fc=[0.8,0,0], alpha=0.0)]
            self.pLoc.append(self.axes[ndx].add_patch(circle[0]))
            
            cpt = self.roi
            rec = [patches.Rectangle((cpt[0],cpt[2]), cpt[1], cpt[3], fill=False, ec = [0.25,0.75,0.25], linewidth=2, linestyle='-',alpha=0.0)]
            self.roirec.append(self.axes[ndx].add_patch(rec[0]))
            
            cpt = self.stimroi
            rec = [patches.Rectangle((cpt[0],cpt[2]), cpt[1], cpt[3], fill=False, ec = [0.5,0.5,0.5], linewidth=2, linestyle='-',alpha=0.0)]
            self.stimrec.append(self.axes[ndx].add_patch(rec[0]))
            
            cpt = self.croproi[ndx]
            self.cropPts.append(cpt)
            rec = [patches.Rectangle((cpt[0],cpt[2]), cpt[1], cpt[3], fill=False, ec = [0.25,0.25,0.75], linewidth=2, linestyle='-',alpha=0.0)]
            self.croprec.append(self.axes[ndx].add_patch(rec[0]))
            
            
            if self.system_cfg['axesRef'] == s:
                self.pelletAxes = self.axes[ndx]
                self.pLoc[ndx].set_center([self.pellet_x,self.pellet_y])
            if self.system_cfg['stimAxes'] == s:
                self.stimAxes = self.axes[ndx]
        
        if self.stimAxes == None:
            self.auto_stim.Enable(False)
            
        self.makeUserList()
        self.figure.canvas.draw()
        
        self.alpha = 0.8
        
        self.canvas.mpl_connect('button_press_event', self.onClick)
        self.Bind(wx.EVT_CHAR_HOOK, self.OnKeyPressed)

        # ---------------- Menu Bar (Config) ----------------
        menubar = wx.MenuBar()
        config_menu = wx.Menu()
        self.menu_softmouse_cfg = config_menu.Append(wx.ID_ANY, 'SoftMouse Config\tCtrl+M')
        self.menu_rfid_cfg = config_menu.Append(wx.ID_ANY, 'RFID Config\tCtrl+R')
        self.menu_session_history = config_menu.Append(wx.ID_ANY, 'Session History\tCtrl+H')
        self.menu_open_external_db = config_menu.Append(wx.ID_ANY, 'Open External DB...')
        menubar.Append(config_menu, '&Config')
        self.SetMenuBar(menubar)
        # Import dialogs now that menu is created (deferred import to keep top clean)
        from acquisition.config_dialogs import SoftMouseConfigDialog, RFIDConfigDialog  # noqa: E402
        self._SoftMouseConfigDialogClass = SoftMouseConfigDialog
        self._RFIDConfigDialogClass = RFIDConfigDialog
        self.Bind(wx.EVT_MENU, self.onOpenSoftMouseConfig, self.menu_softmouse_cfg)
        self.Bind(wx.EVT_MENU, self.onOpenRFIDConfig, self.menu_rfid_cfg)
        self.Bind(wx.EVT_MENU, self.onOpenSessionHistory, self.menu_session_history)
        self.Bind(wx.EVT_MENU, self.onOpenExternalDB, self.menu_open_external_db)
        # After user list (and config) populated, attempt automatic RFID start
        wx.CallAfter(self._start_rfid_listener)

    # ---------------- Dialog open handlers (stubs) ----------------
    def onOpenSoftMouseConfig(self, event):
        try:
            dlg = self._SoftMouseConfigDialogClass(self)
            dlg.ShowModal()
        finally:
            try:
                dlg.Destroy()
            except Exception:
                pass

    def onOpenRFIDConfig(self, event):
        try:
            dlg = self._RFIDConfigDialogClass(self)
            dlg.ShowModal()
        finally:
            try:
                dlg.Destroy()
            except Exception:
                pass

    def onOpenSessionHistory(self, event):
        try:
            if not getattr(self, 'mouse_meta', None) or not self.mouse_meta.get('rfid'):
                wx.MessageBox('No current RFID selected (scan or lookup first).', 'Session History', style=wx.OK|wx.ICON_INFORMATION)
                return
            self._ensure_local_db()
            if not getattr(self, '_db_init_done', False):
                wx.MessageBox('Local database not initialized.', 'Session History', style=wx.OK|wx.ICON_WARNING)
                return
            rfid = self.mouse_meta.get('rfid')
            sessions = self.local_db.list_sessions_for_mouse(rfid, limit=100)
            dlg = SessionHistoryDialog(self, rfid, sessions)
            dlg.ShowModal()
        except Exception as e:
            try:
                self.log.error('Failed opening session history: %s', e)
            except Exception:
                pass
        finally:
            try:
                dlg.Destroy()
            except Exception:
                pass

    def onOpenExternalDB(self, event):
        """Allow user to select an existing dummy or alternate ExperimentDB file and view sessions for an RFID."""
        try:
            with wx.FileDialog(self, message='Select experiment_local.sqlite file', wildcard='SQLite (*.sqlite)|*.sqlite|All files (*.*)|*.*', style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
                if fd.ShowModal() != wx.ID_OK:
                    return
                db_path = fd.GetPath()
            # Validate and load existing DB
            from db.experiment_db import ExperimentDB
            try:
                ext_db = ExperimentDB.from_existing(db_path)
            except Exception as e:
                wx.MessageBox(f'Invalid DB: {e}', 'External DB', style=wx.OK|wx.ICON_ERROR)
                return
            # Ask for RFID to inspect
            rfid_dlg = wx.TextEntryDialog(self, 'Enter 15-char RFID to view sessions:', 'External DB RFID')
            if rfid_dlg.ShowModal() != wx.ID_OK:
                return
            rfid_val = ''.join(ch for ch in rfid_dlg.GetValue().strip() if ch.isalnum())
            if len(rfid_val) != 15:
                wx.MessageBox('RFID must be 15 alphanumeric characters.', 'External DB', style=wx.OK|wx.ICON_WARNING)
                return
            sessions = ext_db.list_sessions_for_mouse(rfid_val, limit=200)
            if not sessions:
                wx.MessageBox(f'No sessions found for RFID {rfid_val}.', 'External DB', style=wx.OK|wx.ICON_INFORMATION)
                return
            # Reuse SessionHistoryDialog if available
            try:
                dlg = SessionHistoryDialog(self, rfid_val, sessions)
                dlg.ShowModal()
            finally:
                try:
                    dlg.Destroy()
                except Exception:
                    pass
        except Exception as e:
            try:
                self.log.error('External DB open failed: %s', e)
            except Exception:
                pass

    #############################################################################################################
    # (Removed inline dialog class definitions; imported above in __init__)

    def write_user_config(self):
        usrdatadir = os.path.dirname(os.path.realpath(__file__))
        configname = os.path.join(usrdatadir, 'Users', self.user_drop.GetStringSelection() + '_userdata.yaml')
        # Ensure base dict exists
        if not hasattr(self, 'user_cfg') or not isinstance(self.user_cfg, dict):
            self.user_cfg = {}
        # Core legacy keys: ensure they exist to avoid KeyErrors elsewhere
        defaults = {
            'waitMin': 0,
            'waitMax': 0,
            'waitCt': 1,
            'protocolSelected': 0,
            'deliveryStyle': 0,
            'waitAfterHand': 1.0,
            'maxWait4Hand': 10.0,
            'minTime2Eat': 2.0,
            'maxTime2Eat': 30.0,
        }
        for k, v in defaults.items():
            self.user_cfg.setdefault(k, v)
        # Persist SoftMouse config section
        sm = self.user_cfg.setdefault('softmouse', {})
        sm['colony'] = getattr(self, '_softmouse_last_colony', sm.get('colony', '')) or ''
        sm['fast'] = bool(getattr(self, '_softmouse_fast_flag', sm.get('fast', False)))
        sm['headful'] = bool(getattr(self, '_softmouse_headful_flag', sm.get('headful', False)))
        sm['force_login'] = bool(getattr(self, '_softmouse_force_login', sm.get('force_login', False)))
        sm['save_state'] = bool(getattr(self, '_softmouse_save_state', sm.get('save_state', False)))
        sm['parse'] = bool(getattr(self, '_softmouse_parse', sm.get('parse', False)))
        # Persist RFID config section
        rfid_cfg = self.user_cfg.setdefault('rfid', {})
        rfid_cfg['port'] = getattr(self, '_rfid_port_value', rfid_cfg.get('port', '')) or ''
        try:
            rfid_cfg['baud'] = int(getattr(self, '_rfid_baud_value', rfid_cfg.get('baud', 9600)) or 9600)
        except Exception:
            rfid_cfg['baud'] = 9600
        rfid_cfg['autostart'] = bool(getattr(self, '_rfid_autostart', rfid_cfg.get('autostart', False)))
        # Persist database section
        db_cfg = self.user_cfg.setdefault('database', {})
        try:
            db_cfg['save_enabled'] = bool(self.save_to_db.GetValue()) if hasattr(self, 'save_to_db') else db_cfg.get('save_enabled', True)
        except Exception:
            db_cfg['save_enabled'] = db_cfg.get('save_enabled', True)
        # Remote endpoint / auth token placeholders (future use)
        db_cfg.setdefault('remote_endpoint', '')
        db_cfg.setdefault('remote_api_token', '')
        try:
            with open(configname, 'w') as cf:
                ruamelFile = ruamel.yaml.YAML()
                ruamelFile.dump(self.user_cfg, cf)
        except Exception as e:
            try:
                self.log.error('Failed writing user config %s: %s', configname, e)
            except Exception:
                pass
            
    def addUser(self, event):
        dlg = wx.TextEntryDialog(self, 'Enter new user initials:', 'Add New User')
        if dlg.ShowModal() == wx.ID_OK:
            new_user = dlg.GetValue()
            usrdatadir = os.path.dirname(os.path.realpath(__file__))
            configname = os.path.join(usrdatadir, 'Users', new_user + '_userdata.yaml')
            with open(configname, 'w') as cf:
                ruamelFile = ruamel.yaml.YAML()
                ruamelFile.dump(self.user_cfg, cf)
            prev_user_path = os.path.join(self.userDir,'prev_user.txt')
            usrdata = open(prev_user_path, 'w')
            usrdata.write(new_user)
            usrdata.close()
            self.makeUserList()
                
        dlg.Destroy()
        
    def selectUser(self, event):
        usrdatadir = os.path.dirname(os.path.realpath(__file__))
        configname = os.path.join(usrdatadir, 'Users', self.user_drop.GetStringSelection() + '_userdata.yaml')
        prev_user_path = os.path.join(self.userDir,'prev_user.txt')
        usrdata = open(prev_user_path, 'w')
        usrdata.write(self.user_drop.GetStringSelection())
        usrdata.close()
        ruamelFile = ruamel.yaml.YAML()
        path = Path(configname)
        if os.path.exists(path):
            with open(path, 'r') as f:
                self.user_cfg = ruamelFile.load(f)
        else:
            # Create default user config when file doesn't exist
            self.user_cfg = {
                'waitMin': 0,
                'waitMax': 0,
                'waitCt': 1,
                'protocolSelected': 0,
                'deliveryStyle': 0,
                'waitAfterHand': 1.0,
                'maxWait4Hand': 10.0,
                'minTime2Eat': 2.0,
                'maxTime2Eat': 30.0
            }
            
        self.tone_delay_min.SetValue(int(self.user_cfg['waitMin']))
        self.tone_delay_max.SetValue(int(self.user_cfg['waitMax']))
        self.delay_count.SetValue(int(self.user_cfg['waitCt']))
        self.make_delay_iters()
        self.protocol.SetSelection(self.user_cfg['protocolSelected'])
        self.setProtocol(None)
        self.setDelStyle()
        # ---- Load softmouse section into attributes ----
        sm = self.user_cfg.get('softmouse') or {}
        self._softmouse_last_colony = sm.get('colony', '') or ''
        self._softmouse_fast_flag = bool(sm.get('fast', False))
        self._softmouse_headful_flag = bool(sm.get('headful', False))
        self._softmouse_force_login = bool(sm.get('force_login', False))
        self._softmouse_save_state = bool(sm.get('save_state', False))
        self._softmouse_parse = bool(sm.get('parse', True))
        # ---- Load rfid section into attributes ----
        rfid_cfg = self.user_cfg.get('rfid') or {}
        self._rfid_port_value = rfid_cfg.get('port', '') or ''
        try:
            self._rfid_baud_value = int(rfid_cfg.get('baud', 9600) or 9600)
        except Exception:
            self._rfid_baud_value = 9600
        self._rfid_autostart = bool(rfid_cfg.get('autostart', False))
        # ---- Load database section ----
        db_cfg = self.user_cfg.get('database') or {}
        if hasattr(self, 'save_to_db'):
            try:
                self.save_to_db.SetValue(bool(db_cfg.get('save_enabled', True)))
            except Exception:
                pass
        # Mirror to legacy widgets if present
        try:
            if hasattr(self, 'rfid_port') and isinstance(self.rfid_port, wx.TextCtrl) and self._rfid_port_value:
                self.rfid_port.SetValue(self._rfid_port_value)
            if hasattr(self, 'rfid_baud'):
                try:
                    self.rfid_baud.SetValue(str(self._rfid_baud_value))
                except Exception:
                    pass
        except Exception:
            pass

    def lookupRFID(self, event):
        """Lookup RFID from in-memory animals export only (no external service)."""
        tag_raw = self.rfid_input.GetValue().strip()
        tag = ''.join(c for c in tag_raw if c.isalnum())
        if not tag:
            self.statusbar.SetStatusText('No RFID entered')
            self.log.warning('Lookup attempt with empty / non-alphanumeric RFID input raw=%r', tag_raw)
            return
        if not hasattr(self, 'animal_metadata'):
            self.statusbar.SetStatusText('Animals metadata not loaded')
            self.log.warning('RFID lookup attempted without animals metadata loaded')
            return
        df = self.animal_metadata
        match_row = None
        for col in df.columns:
            if 'rfid' in col.lower() or 'tag' in col.lower():
                subset = df[df[col].astype(str)==tag]
                if len(subset) == 1:
                    match_row = subset.iloc[0]
                    break
        if match_row is not None:
            self.mouse_meta = match_row.to_dict()
            self.mouse_meta['rfid'] = tag
            self.active_rfid = tag
            name = self.mouse_meta.get('Name') or self.mouse_meta.get('name') or '(unnamed)'
            self.statusbar.SetStatusText(f'RFID {tag} matched {name}')
            self.log.info('RFID lookup matched tag=%s name=%s', tag, name)
        else:
            self.statusbar.SetStatusText(f'RFID {tag} not found in current table')
            self.log.info('RFID %s not found in animals metadata', tag)

    def write_metalink_entry(self):
        """Append a metalink entry linking RFID/mouse metadata to this session.

        Creates temp/metalink.txt with one JSON object per line:
        {"rfid":..., "session_dir":..., "session_name":..., "raw_meta":..., "timestamp":..., "mouse":{...}}
        """
        import json, pathlib, datetime as _dt
        if not getattr(self, 'mouse_meta', None):
            return
        rfid = self.mouse_meta.get('rfid') or self.active_rfid
        if not rfid:
            return
        sess_name = os.path.basename(self.sess_dir)
        entry = {
            "rfid": rfid,
            "session_dir": self.sess_dir,
            "session_name": sess_name,
            "raw_meta": self.metapath,
            "timestamp": _dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            "mouse": self.mouse_meta,
        }
        tmp_dir = pathlib.Path(__file__).parent / 'temp'
        tmp_dir.mkdir(exist_ok=True)
        metalink_path = tmp_dir / 'metalink.txt'
        with metalink_path.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry) + '\n')
        self.log.info('Metalink appended rfid=%s session=%s', rfid, sess_name)

    # ---------------- SoftMouse animals metadata export (background) -----------------
    def startAnimalExport(self, event):
        if getattr(self, 'animal_export_active', False):
            self.log.warning('Animal export already running')
            return
        # Retrieve colony name from dialog-sourced attribute or fallback to blank
        colony = ''
        if hasattr(self, 'softmouse_colony') and isinstance(self.softmouse_colony, wx.TextCtrl):
            try:
                colony = self.softmouse_colony.GetValue().strip()
            except Exception:
                colony = ''
        if not colony and hasattr(self, '_softmouse_last_colony'):
            colony = (self._softmouse_last_colony or '').strip()
        if not colony:
            self.statusbar.SetStatusText('Enter colony name for SoftMouse export')
            return
        try:
            import multiprocessing as mp
            from automation.export_runner import run_export
        except Exception as e:
            self.statusbar.SetStatusText(f'Export module error: {e}')
            self.log.exception('Failed importing export runner: %s', e)
            return
        self.animal_export_queue = mp.Queue()
        self.animal_export_proc = mp.Process(target=run_export, args=(
            colony,
            bool(getattr(self, 'softmouse_fast', None) and self.softmouse_fast.GetValue()) if hasattr(self,'softmouse_fast') else bool(getattr(self,'_softmouse_fast_flag', False)),
            bool(getattr(self, 'softmouse_headful', None) and self.softmouse_headful.GetValue()) if hasattr(self,'softmouse_headful') else bool(getattr(self,'_softmouse_headful_flag', False)),
            'softmouse_storage_state.json',
            'downloads_animals',
            self.animal_export_queue,
        ), daemon=True)
        self.animal_export_proc.start()
        self.animal_export_active = True
        self.softmouse_status.SetLabel('Animals: loading...')
        self.statusbar.SetStatusText('SoftMouse export started')
        if not hasattr(self, 'backgroundTimer'):
            self.backgroundTimer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.pollBackground, self.backgroundTimer)
        if not self.backgroundTimer.IsRunning():
            self.backgroundTimer.Start(500)

    def pollBackground(self, event):
        # Called by backgroundTimer to poll child processes/queues
        updated = False
        # Animal export results
        if getattr(self, 'animal_export_active', False):
            try:
                if self.animal_export_queue.qsize() > 0:
                    res = self.animal_export_queue.get_nowait()
                else:
                    res = None
            except Exception:
                res = None
            if res is not None:
                self.animal_export_active = False
                try:
                    if self.animal_export_proc.is_alive():
                        self.animal_export_proc.join(timeout=0.2)
                except Exception:
                    pass
                if res.get('ok') and res.get('file'):
                    self._load_animals_file(res['file'])
                else:
                    err = res.get('error', 'unknown error')
                    self.softmouse_status.SetLabel('Animals: failed')
                    self.statusbar.SetStatusText(f'Export failed: {err}')
                    self.log.error('SoftMouse export failed: %s', err)
                updated = True
        if updated:
            self.figure.canvas.draw_idle()
        # If nothing left to poll, stop timer
        # RFID events
        if getattr(self, 'rfid_listening', False):
            try:
                while self.rfid_queue.qsize() > 0:
                    evt = self.rfid_queue.get_nowait()
                    if 'error' in evt:
                        self.rfid_status.SetLabel(f"RFID error: {evt['error']}")
                        self.statusbar.SetStatusText(f"RFID error: {evt['error']}")
                        self.log.error('RFID listener error: %s', evt['error'])
                        self._stop_rfid_listener()
                        break
                    tag = evt.get('tag')
                    if tag:
                        self._handle_rfid_tag(tag)
            except Exception:
                pass
        if not getattr(self, 'animal_export_active', False) and not getattr(self, 'rfid_listening', False):
            try:
                if self.backgroundTimer.IsRunning():
                    self.backgroundTimer.Stop()
            except Exception:
                pass

    def _load_animals_file(self, path):
        from pathlib import Path
        import pandas as pd
        try:
            p = Path(path)
            if p.suffix.lower() == '.csv':
                df = pd.read_csv(p)
            else:
                df = pd.read_excel(p)
            self.animal_metadata = df
            self.softmouse_status.SetLabel(f'Animals: {len(df)} rows')
            self.statusbar.SetStatusText(f'Loaded animals metadata ({len(df)} rows)')
            self.log.info('Animals metadata loaded rows=%d', len(df))
        except Exception as e:
            self.softmouse_status.SetLabel('Animals: parse error')
            self.statusbar.SetStatusText(f'Animals parse error: {e}')
            self.log.exception('Failed parsing animals file: %s', e)

    def _start_rfid_listener(self):
        try:
            self.log.info('Attempting RFID listener start')
        except Exception:
            pass
        if getattr(self, 'rfid_listening', False):
            return
        port = (getattr(self, '_rfid_port_value', '') or '').strip()
        if not port:
            # Inform user; keep toggle off
            try:
                self.statusbar.SetStatusText('RFID: no port configured')
                self.log.warning('RFID start aborted (no configured port)')
            except Exception:
                pass
            if hasattr(self, 'rfid_toggle'):
                try:
                    self.rfid_toggle.SetValue(False)
                except Exception:
                    pass
            return
        else:
            try:
                self.log.info('RFID starting using configured port=%s baud_candidate=%s', port, getattr(self, '_rfid_baud_value', 'unknown'))
            except Exception:
                pass
        try:
            baud = int(getattr(self, '_rfid_baud_value', 9600))
        except Exception:
            baud = 9600
        if not hasattr(self, 'animal_metadata'):
            try:
                self.log.info('RFID starting without animals metadata loaded yet')
            except Exception:
                pass
        try:
            import multiprocessing as mp
            from rfid.rfid_listener_process import run_rfid_listener
        except Exception as e:
            try:
                self.statusbar.SetStatusText(f'RFID import error: {e}')
                self.log.error('RFID module import error: %s', e)
            except Exception:
                pass
            return
        self.rfid_queue = mp.Queue()
        self.rfid_stop = mp.Event()
        self.rfid_proc = mp.Process(target=run_rfid_listener, args=(port, baud, self.rfid_queue, self.rfid_stop), daemon=True)
        try:
            self.rfid_proc.start()
        except Exception as e:
            self.statusbar.SetStatusText(f'RFID start error: {e}')
            try:
                self.log.error('RFID process start failure port=%s baud=%s err=%r', port, baud, e)
            except Exception:
                pass
            return
        self.rfid_listening = True
        self.rfid_status.SetLabel('RFID listening...')
        if not hasattr(self, 'backgroundTimer'):
            self.backgroundTimer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.pollBackground, self.backgroundTimer)
        if not self.backgroundTimer.IsRunning():
            self.backgroundTimer.Start(500)
        self.statusbar.SetStatusText('RFID listener started')
        try:
            self.log.info('RFID listener started port=%s baud=%s', port, baud)
        except Exception:
            pass
        if hasattr(self, 'rfid_toggle'):
            try:
                self.rfid_toggle.SetValue(True)
            except Exception:
                pass

    def _stop_rfid_listener(self):
        if not getattr(self, 'rfid_listening', False):
            return
        try:
            self.rfid_stop.set()
        except Exception:
            pass
        try:
            if self.rfid_proc.is_alive():
                self.rfid_proc.join(timeout=1.0)
        except Exception:
            pass
        self.rfid_listening = False
        self.rfid_status.SetLabel('RFID idle')
        self.statusbar.SetStatusText('RFID listener stopped')
        self.log.info('RFID listener stopped')
        if hasattr(self, 'rfid_toggle'):
            try:
                self.rfid_toggle.SetValue(False)
            except Exception:
                pass

    def _on_rfid_toggle(self, event):
        # User-driven toggle
        if self.rfid_toggle.GetValue():
            self._start_rfid_listener()
        else:
            self._stop_rfid_listener()


    def _populate_from_last_session(self, rfid: str):
        self._ensure_local_db()
        if not getattr(self, '_db_init_done', False):
            return
        try:
            last = self.local_db.last_session_for_mouse(rfid)
            if not last:
                return
            # Pre-fill prerecord dialog context for next session
            self._last_session_context = last
        except Exception:
            pass

    def _start_db_session_if_needed(self, rfid: str, prerecord_ctx: dict, was_live_only: bool, session_dir, yaml_path):
        self._ensure_local_db()
        if not getattr(self, '_db_init_done', False):
            return
        try:
            self.local_db.ensure_mouse(rfid, softmouse_payload=getattr(self, 'mouse_meta', None))
            self._active_session_id = self.local_db.start_session(rfid, prerecord_ctx, was_live_only=was_live_only, session_dir=session_dir, metadata_yaml_path=yaml_path)
            self._live_only_active = was_live_only
        except Exception as e:
            try:
                self.log.error('Failed starting DB session: %s', e)
            except Exception:
                pass

    def _update_db_session_paths(self, session_dir: str, yaml_path: str):
        if not getattr(self, '_active_session_id', None):
            return
        if not getattr(self, '_db_init_done', False):
            return
        try:
            # direct SQL update (no public method earlier to keep API small)
            import sqlite3, datetime as _dt
            now = _dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
            cx = sqlite3.connect(self.local_db.db_path)
            with cx:
                cx.execute('UPDATE sessions SET session_dir=?, metadata_yaml_path=?, updated_utc=? WHERE id=?', (session_dir, yaml_path, now, self._active_session_id))
            cx.close()
        except Exception:
            pass

    def _finalize_db_session(self, post_ctx, session_notes):  # types: Optional[dict]
        if not getattr(self, '_active_session_id', None):
            return
        if not getattr(self, '_db_init_done', False):
            return
        try:
            self.local_db.finalize_session(self._active_session_id, post_ctx, session_notes=session_notes)
        except Exception as e:
            try:
                self.log.error('Failed finalizing DB session: %s', e)
            except Exception:
                pass
        finally:
            self._active_session_id = None
            self._live_only_active = False

    def _sync_remote_if_requested(self):
        # Called on compression to push unsynced rows
        self._ensure_local_db()
        if not getattr(self, '_db_init_done', False):
            return 0
        try:
            from db.experiment_db import RemoteSyncClient, push_unsynced
            remote = RemoteSyncClient()  # TODO: parameterize endpoint
            pushed = push_unsynced(self.local_db, remote)
            if pushed:
                self.log.info('Remote sync pushed %d sessions', pushed)
            return pushed
        except Exception as e:
            try:
                self.log.error('Remote sync failed: %s', e)
            except Exception:
                pass
            return 0

    def _ensure_local_db(self):
        if self._db_init_done:
            return
        try:
            from db.experiment_db import ExperimentDB
            root = os.path.join(os.path.dirname(os.path.realpath(__file__)), '..')
            root = os.path.abspath(root)
            # Determine mirror path: platform-specific default pattern
            mirror_path = None
            try:
                unit_ref = self.system_cfg.get('unitRef', 'UnitUnknown') if hasattr(self, 'system_cfg') else 'UnitUnknown'
                if os.name == 'nt':
                    # Windows network drive (legacy)
                    base_default = r'Z:\PHYS\ChristieLab\Data\ReachingData\ExperimentLogs'
                elif os.name == 'posix':
                    # Linux / Isilon mount path
                    base_default = '/mnt/isilon/Data/ReachingData/ExperimentLogs'
                # Allow environment override (SOFTMOUSE_MIRROR_BASE)
                base_default = os.environ.get('SOFTMOUSE_MIRROR_BASE', base_default)
                default_mirror = os.path.join(base_default, unit_ref)
                # If user config previously saved a custom mirror_dir, reuse it
                saved_mirror = None
                if hasattr(self, 'user_cfg') and isinstance(self.user_cfg, dict):
                    saved_mirror = (self.user_cfg.get('database', {}) or {}).get('mirror_dir')
                if saved_mirror and os.path.isdir(saved_mirror):
                    mirror_path = saved_mirror
                elif os.path.isdir(default_mirror):
                    mirror_path = default_mirror
                else:
                    # Prompt user to select a directory for mirrored logs
                    dlg = wx.DirDialog(self, message='Select directory to store mirrored experiment logs (second DB copy)',
                                       style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
                    if dlg.ShowModal() == wx.ID_OK:
                        candidate = dlg.GetPath()
                        if os.path.isdir(candidate):
                            mirror_path = candidate
                            # Persist choice in user config
                            try:
                                if not hasattr(self, 'user_cfg') or not isinstance(self.user_cfg, dict):
                                    self.user_cfg = {}
                                db_cfg = self.user_cfg.setdefault('database', {})
                                db_cfg['mirror_dir'] = mirror_path
                                self.write_user_config()
                            except Exception:
                                pass
                    dlg.Destroy()
            except Exception:
                mirror_path = None
            self.local_db = ExperimentDB(root, mirror_dir=mirror_path)
            self._db_init_done = True
            self.log.info('Local experiment DB initialized path=%s', getattr(self.local_db, 'db_path', '?'))
            if mirror_path:
                try:
                    self.log.info('Mirror experiment DB path=%s', os.path.join(mirror_path, 'experiment_local.sqlite'))
                except Exception:
                    pass
        except Exception as e:
            try:
                self.log.error('Failed initializing local DB: %s', e)
            except Exception:
                pass
            self._db_init_done = False

    def _handle_rfid_tag(self, tag: str):
        # Sanitize and enforce alphanumeric tag
        raw = tag
        tag = ''.join(ch for ch in tag if ch.isalnum())
        if not tag or not tag.isalnum():
            self.log.warning('Discarding non-alphanumeric RFID input raw=%r sanitized=%r', raw, tag)
            return
        # Lookup tag within loaded animals metadata if present
        import pandas as pd
        name = None
        if hasattr(self, 'animal_metadata'):
            df = self.animal_metadata
            for col in df.columns:
                if 'rfid' in col.lower() or 'tag' in col.lower():
                    matches = df[df[col].astype(str)==tag]
                    if len(matches) == 1:
                        # capture row as dict
                        self.mouse_meta = matches.iloc[0].to_dict()
                        self.mouse_meta['rfid'] = tag
                        name = self.mouse_meta.get('Name') or self.mouse_meta.get('name')
                        break
        # If no match locally and user wants DB integration, attempt SoftMouse refresh on-demand (placeholder)
        if not name and getattr(self, 'save_to_db', None) and self.save_to_db.GetValue():
            try:
                # Trigger a background export if animals metadata absent or stale
                if not hasattr(self, 'animal_metadata') or getattr(self, 'animal_export_active', False) is False:
                    # Could call startAnimalExport but we need colony; rely on existing colony stored
                    if hasattr(self, '_softmouse_last_colony') and self._softmouse_last_colony:
                        self.startAnimalExport(None)
            except Exception:
                pass
        msg = f'RFID tag {tag}' + (f' matched {name}' if name else ' (no match)')
        self.rfid_status.SetLabel(msg[:30])
        self.statusbar.SetStatusText(msg)
        self.log.info('RFID tag read tag=%s match=%s', tag, bool(name))
        if name:
            dlg = wx.MessageDialog(parent=None, message=f'RFID {tag} matched {name}. Use this mouse?', caption='RFID Match', style=wx.YES_NO|wx.ICON_INFORMATION)
            if dlg.ShowModal() == wx.ID_YES:
                self.log.info('RFID %s accepted by user', tag)
            else:
                self.log.info('RFID %s rejected by user', tag)
            dlg.Destroy()
        # Populate last session context if any
        try:
            self._populate_from_last_session(tag)
        except Exception:
            pass
        
    def setProtocol(self, event):
        self.proto_str = self.protocol.GetStringSelection()
        self.user_cfg['protocolSelected'] = self.protocol.GetSelection()
        self.write_user_config()
        self.stim_selection.value = self.user_cfg['protocolSelected']
        
        if self.com.value < 0:
            return
        self.com.value = 5
        while self.com.value > 0:
            time.sleep(0.01)
        
    def toggleStyle(self, event):
        if self.user_cfg['deliveryStyle'] == 1:
            self.user_cfg['deliveryStyle'] = 0
        else:
            self.user_cfg['deliveryStyle'] = 1
        self.setDelStyle()
        
    def setDelStyle(self):
        if self.user_cfg['deliveryStyle'] == 1:
            self.del_style.value = 1
            self.toggle_style.SetLabel("Style B")
        else:
            self.del_style.value = 0
            self.toggle_style.SetLabel("Style A")
        self.write_user_config()
        
        if self.com.value < 0:
            return
        self.com.value = 15
        while self.com.value > 0:
            time.sleep(0.01)
        
    def makeUserList(self):
        usrdatadir = os.path.dirname(os.path.realpath(__file__))
        self.userDir = os.path.join(usrdatadir, 'Users')
        if not os.path.isdir(self.userDir):
            os.mkdir(self.userDir)
        user_list = [name for name in os.listdir(self.userDir) if name.endswith('.yaml')]
        user_list = [name[:-14] for name in user_list]
        self.current_user = 'Default'
        if not len(user_list):
            user_list = [self.current_user]
        else:
            if 'Default' in user_list:
                user_list.remove('Default')
            user_list = [self.current_user]+user_list
        self.user_drop.SetItems(user_list)
        prev_user_path = os.path.join(self.userDir,'prev_user.txt')
        self.user_drop.SetSelection(0)
        if os.path.isfile(prev_user_path):
            usrdata = open(prev_user_path, 'r')
            self.current_user = usrdata.readline().strip()
            usrdata.close()
            if self.current_user in user_list:
                self.user_drop.SetStringSelection(self.current_user)
        self.selectUser(None)
        
    def autoPellet(self, event):
        if self.auto_pellet.GetValue():
            self.com.value = 9
            while self.com.value > 0:
                time.sleep(0.01)
        else:
            self.com.value = 10
            while self.com.value > 0:
                time.sleep(0.01)
        
    def make_delay_iters(self):
        minval = int(self.tone_delay_min.GetValue())
        maxval = int(self.tone_delay_max.GetValue())
        ctval = int(self.delay_count.GetValue())
        self.delay_values = np.linspace(minval, maxval, ctval)
        np.random.shuffle(self.delay_values)
        self.first_delay = -1
        print('New delay list:')
        print(self.delay_values)
            
    def comFun(self, event):
      # case 'A': //servoMax
      # case 'B': //servoMin
      # case 'C': //servoBaseVal
      # case 'D': // Set tone duration (ms)
      # case 'F': // Set tone frequency
      # case 'T': // Play tone 
      # case 'E': // No solenoid
      # case 'I': // Solenoid in
      # case 'O': // Solenoid out
      # case 'U': // Solenoids neutral
      # case 'Y': // Trigger solenoid
      # case 'P': // Get proximity reading
      # case 'L': // Load pellets into reservoir
      # case 'R': // Drop elevator to reveal pellet
      # case 'Q': // Raise elevator to load a single pellet
      
        if self.com.value < 0:
            return
        waitval = 0
        while not self.com.value == 0:
            time.sleep(1)
            waitval+=1
            if waitval > 10:
                break
        evobj = event.GetEventObject()
        if self.send_home == evobj:
            self.com.value = 1
        elif self.load_pellet == evobj:
            self.com.value = 2
        elif self.send_pellet == evobj:
            self.com.value = 3
        elif self.trig_release == evobj:
            self.com.value = 4
        elif self.tone_delay_min == evobj:
            self.user_cfg['waitMin'] = int(self.tone_delay_min.GetValue())
            self.write_user_config()
            self.make_delay_iters()
        elif self.delay_count == evobj:
            self.user_cfg['waitCt'] = int(self.delay_count.GetValue())
            self.write_user_config()
            if self.user_cfg['waitCt'] == 1:
                self.tone_delay_max.Enable(False)
            else:
                self.tone_delay_max.Enable(True)
            self.make_delay_iters()
        elif self.tone_delay_max == evobj:
            self.user_cfg['waitMax'] = int(self.tone_delay_max.GetValue())
            self.write_user_config()
            self.make_delay_iters()
        elif self.auto_delay == evobj:
            if self.auto_delay.GetValue():
                self.make_delay_iters()
        elif self.Xmag == evobj:
            self.mVal.value = self.Xmag.GetValue()
            self.com.value = 12
        elif self.Ymag == evobj:
            self.mVal.value = self.Ymag.GetValue()
            self.com.value = 13
            self.pellet_x = self.system_cfg['pelletXY'][0]-self.Ymag.GetValue()*self.system_cfg['shiftFactor']
            ndx = self.axes.index(self.pelletAxes)
            self.pLoc[ndx].set_center([self.pellet_x,self.pellet_y])
        elif self.Zmag == evobj:
            self.mVal.value = self.Zmag.GetValue()
            self.com.value = 14
            self.pellet_y = self.system_cfg['pelletXY'][1]-self.Zmag.GetValue()*self.system_cfg['shiftFactor']
            ndx = self.axes.index(self.pelletAxes)
            self.pLoc[ndx].set_center([self.pellet_x,self.pellet_y])
        elif self.send_stim == evobj:
            self.com.value = 16
        
    def setCrop(self, event):
        self.widget_panel.Enable(False)
        
    def OnKeyPressed(self, event):
        # print(event.GetModifiers())
        # print(event.GetKeyCode())
        x = 0
        y = 0
        if event.GetKeyCode() == wx.WXK_RETURN or event.GetKeyCode() == wx.WXK_NUMPAD_ENTER:
            if self.set_pellet_pos.GetValue():
                self.system_cfg['pelletXY'][0] = self.pellet_x
                self.system_cfg['pelletXY'][1] = self.pellet_y
            elif self.set_roi.GetValue():
                self.system_cfg['roiXWYH'] = np.ndarray.tolist(self.roi)
            elif self.set_stim.GetValue():
                self.system_cfg['stimXWYH'] = np.ndarray.tolist(self.stimroi)
            elif self.set_crop.GetValue():
                ndx = self.axes.index(self.cropAxes)
                s = self.camStrList[ndx]
                self.system_cfg[s]['crop'] = np.ndarray.tolist(self.croproi[ndx])
        
            clara.write_config(self.system_cfg)
            self.set_pellet_pos.SetValue(False)
            self.set_roi.SetValue(False)
            self.set_stim.SetValue(False)
            self.set_crop.SetValue(False)
            self.widget_panel.Enable(True)
            self.play.SetFocus()
        elif self.set_pellet_pos.GetValue() or self.set_roi.GetValue() or self.set_crop.GetValue() or self.set_stim.GetValue():
            if event.GetKeyCode() == 314: #LEFT
                x = -1
                y = 0
            elif event.GetKeyCode() == 316: #RIGHT
                x = 1
                y = 0
            elif event.GetKeyCode() == 315: #UP
                x = 0
                y = -1
            elif event.GetKeyCode() == 317: #DOWN
                x = 0
                y = 1
            elif event.GetKeyCode() == 127: #DELETE
                if self.set_crop.GetValue():
                    ndx = self.axes.index(self.cropAxes)
                    self.croproi[ndx][0] = 0
                    self.croproi[ndx][2] = 0
                    for ndx in range(self.camCt):
                        self.croprec[ndx].set_alpha(0)
                    clara.write_config(self.system_cfg)
                    self.set_crop.SetValue(False)
                    self.widget_panel.Enable(True)
                    self.play.SetFocus()
                    self.figure.canvas.draw()
                elif self.set_stim.GetValue():
                    self.system_cfg['stimAxes'] = 'None'
                    self.stimAxes = None
                    for ndx in range(self.camCt):
                        self.stimrec[ndx].set_alpha(0)
                    self.stimroi[0] = 0
                    self.stimroi[2] = 0
                    clara.write_config(self.system_cfg)
                    self.set_stim.SetValue(False)
                    self.widget_panel.Enable(True)
                    self.play.SetFocus()
                    self.figure.canvas.draw()
        else:
            event.Skip()
            
        if self.set_pellet_pos.GetValue():
            self.pellet_x+=x
            self.pellet_y+=y
            self.drawROI()
        elif self.set_roi.GetValue():
            self.roi[0]+=x
            self.roi[2]+=y
            self.drawROI()
        elif self.set_stim.GetValue():
            self.stimroi[0]+=x
            self.stimroi[2]+=y
            self.drawROI()
        elif self.set_crop.GetValue():
            ndx = self.axes.index(self.cropAxes)
            self.croproi[ndx][0]+=x
            self.croproi[ndx][2]+=y
            self.drawROI()
            
            
        if self.set_crop.GetValue():
            ndx = self.axes.index(self.cropAxes)
            self.croproi[ndx][0]+=x
            self.croproi[ndx][2]+=y
            self.drawROI()
            
    def drawROI(self):
        ndx = self.axes.index(self.pelletAxes)
        if self.set_pellet_pos.GetValue():
            self.pLoc[ndx].set_center([self.pellet_x,self.pellet_y])
            self.pLoc[ndx].set_alpha(0.6)
        elif self.set_roi.GetValue():
            self.roirec[ndx].set_x(self.roi[0])
            self.roirec[ndx].set_y(self.roi[2])
            self.roirec[ndx].set_width(self.roi[1])
            self.roirec[ndx].set_height(self.roi[3])
            self.roirec[ndx].set_alpha(0.6)
        elif self.set_stim.GetValue():
            ndx = self.axes.index(self.stimAxes)
            self.stimrec[ndx].set_x(self.stimroi[0])
            self.stimrec[ndx].set_y(self.stimroi[2])
            self.stimrec[ndx].set_width(self.stimroi[1])
            self.stimrec[ndx].set_height(self.stimroi[3])
            self.stimrec[ndx].set_alpha(0.6)
        elif self.set_crop.GetValue():
            ndx = self.axes.index(self.cropAxes)
            self.croprec[ndx].set_x(self.croproi[ndx][0])
            self.croprec[ndx].set_y(self.croproi[ndx][2])
            self.croprec[ndx].set_width(self.croproi[ndx][1])
            self.croprec[ndx].set_height(self.croproi[ndx][3])
            if not self.croproi[ndx][0] == 0:
                self.croprec[ndx].set_alpha(0.6)
        self.figure.canvas.draw()
        
        
    def onClick(self,event):
        if self.set_pellet_pos.GetValue():
            for ndx in range(self.camCt):
                self.pLoc[ndx].set_alpha(0.0)

            self.system_cfg = clara.read_config()
            if self.stimAxes == event.inaxes:
                print('Stimulus camera must not be the pellet-detecting camera')
                self.set_pellet_pos.SetValue(False)
                self.widget_panel.Enable(True)
                return
            ndx = self.axes.index(event.inaxes)
            self.pelletAxes = event.inaxes
            self.system_cfg['axesRef'] = self.camStrList[ndx]
            self.pellet_x = int(event.xdata)
            self.pellet_y = int(event.ydata)
        elif self.set_roi.GetValue():
            for ndx in range(self.camCt):
                self.roirec[ndx].set_alpha(0.0)
                
            self.system_cfg = clara.read_config()
            if self.stimAxes == event.inaxes:
                print('Stimulus camera must not be the pellet-detecting camera')
                self.set_roi.SetValue(False)
                self.widget_panel.Enable(True)
                return
            ndx = self.axes.index(event.inaxes)
            self.pelletAxes = event.inaxes
            self.system_cfg['axesRef'] = self.camStrList[ndx]
            self.roi = np.asarray(self.system_cfg['roiXWYH'], int)
            roi_x = event.xdata
            roi_y = event.ydata
            self.roi = np.asarray([roi_x-self.roi[1]/2,self.roi[1],roi_y-self.roi[3]/2,self.roi[3]], int)
        elif self.set_stim.GetValue():
            for ndx in range(self.camCt):
                self.stimrec[ndx].set_alpha(0.0)
            
            self.system_cfg = clara.read_config()
            if self.pelletAxes == event.inaxes:
                print('Stimulus camera must not be the pellet-detecting camera')
                self.set_stim.SetValue(False)
                self.widget_panel.Enable(True)
                return
            ndx = self.axes.index(event.inaxes)
            self.stimAxes = event.inaxes
            self.system_cfg['stimAxes'] = self.camStrList[ndx]
            self.stimroi = np.asarray(self.system_cfg['stimXWYH'], int)
            roi_x = event.xdata
            roi_y = event.ydata
            self.stimroi = np.asarray([roi_x-self.stimroi[1]/2,self.stimroi[1],roi_y-self.stimroi[3]/2,self.stimroi[3]], int)
        elif self.set_crop.GetValue():
            for ndx in range(self.camCt):
                self.croprec[ndx].set_alpha(0.0)
                
            self.system_cfg = clara.read_config()
            self.cropAxes = event.inaxes
            ndx = self.axes.index(event.inaxes)
            s = self.camStrList[ndx]
            self.croproi[ndx] = self.system_cfg[s]['crop']
            roi_x = event.xdata
            roi_y = event.ydata
            self.croproi[ndx] = np.asarray([roi_x-self.croproi[ndx][1]/2,self.croproi[ndx][1],
                                            roi_y-self.croproi[ndx][3]/2,self.croproi[ndx][3]], int)
        self.drawROI()       
            
    def compressVid(self, event):
        ok2compress = False
        try:
            if not self.mv.is_alive():
                self.mv.terminate()   
                ok2compress = True
            else:
                if wx.MessageBox("Compress when transfer completes?", caption="Abort", style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION):
                    while self.mv.is_alive():
                        time.sleep(10)
                    self.mv.terminate()   
                    ok2compress = True
        except:
            ok2compress = True
        
        
        if ok2compress:
            print('\n\n---- Please DO NOT close this GUI until compression is complete!!! ----\n\n')
            self.mv = clara.moveVids()
            self.mv.start()
            while self.mv.is_alive():
                time.sleep(10)
            self.mv.terminate()   
            
            compressThread = compressVideos.CLARA_compress()
            compressThread.start()
            self.compress_vid.Enable(False)
            # After launching compression, attempt remote sync (non-blocking best-effort)
            try:
                pushed = self._sync_remote_if_requested()
                if pushed:
                    self.statusbar.SetStatusText(f'Remote synced {pushed} sessions')
            except Exception:
                pass
    
    def camReset(self,event):
        self.initThreads()
        self.camaq.value = 2
        self.startAq()
        time.sleep(3)
        self.stopAq()
        self.deinitThreads()
        print('\n*** CAMERAS RESET ***\n')
    
    def runExpt(self,event):
        print('todo')
    def exptID(self,event):
        pass
        
    def liveFeed(self, event):
        if self.play.GetLabel() == 'Abort':
            self.rec.SetValue(False)
            self.recordCam(event)
            
            if wx.MessageBox("Are you sure?", caption="Abort", style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION):
                shutil.rmtree(self.sess_dir)
                time.sleep(5)
            self.play.SetValue(False)
                        
        elif self.play.GetValue() == True:
            # Stop RFID listener during live acquisition
            try:
                if getattr(self, 'rfid_listening', False):
                    self._stop_rfid_listener()
            except Exception:
                pass
            if not self.liveTimer.IsRunning():
                if not self.pellet_x == 0:
                    if not self.roi[0] == 0:
                        self.pellet_timing = time.time()
                        self.pellet_status = 3
                self.camaq.value = 1
                self.startAq()
                self.liveTimer.Start(150)
            self.rec.Enable(False)
            for h in self.disable4cam:
                h.Enable(False)
        else:
            if self.liveTimer.IsRunning():
                self.liveTimer.Stop()
            self.stopAq()
            time.sleep(2)
            self.play.SetLabel('Live')
            # Restart RFID listener after exiting live mode (if not recording)
            try:
                if not self.rec.GetValue():
                    self._start_rfid_listener()
            except Exception:
                pass

        self.rec.Enable(True)
        for h in self.disable4cam:
            h.Enable(True)
        # Live-only prerecord (start) logic
        if self.play.GetValue() and hasattr(self, 'save_to_db') and self.save_to_db.GetValue():
            if getattr(self, 'mouse_meta', None) and not getattr(self, '_live_only_active', False):
                try:
                    dlg = PreRecordDialog(self)
                    if hasattr(self, '_last_session_context') and self._last_session_context and self._last_session_context.get('prerecord'):
                        try:
                            prev = self._last_session_context['prerecord']
                            dlg.choice_record_type.SetStringSelection(prev.get('recording_type', dlg.choice_record_type.GetStringSelection()))
                            dlg.choice_modality.SetStringSelection(prev.get('modality', dlg.choice_modality.GetStringSelection()))
                            dlg.txt_task.SetValue(prev.get('task_protocol',''))
                            dlg.txt_notes.SetValue(prev.get('pre_notes',''))
                        except Exception:
                            pass
                    if dlg.ShowModal() == wx.ID_OK:
                        ctx = dlg.get_values()
                        self._prerecord_context = ctx
                        rfid = self.mouse_meta.get('rfid') if isinstance(self.mouse_meta, dict) else None
                        if rfid:
                            self._start_db_session_if_needed(rfid, ctx, was_live_only=True, session_dir=None, yaml_path=None)
                    dlg.Destroy()
                except Exception:
                    pass
        # Live-only finalize (stop) logic
        if (not self.play.GetValue()) and getattr(self, '_live_only_active', False) and getattr(self, '_active_session_id', None):
            post_ctx = None
            try:
                dlg2 = PostRecordDialog(self, getattr(self, '_prerecord_context', None))
                if dlg2.ShowModal() == wx.ID_OK:
                    post_ctx = dlg2.get_values()
                dlg2.Destroy()
            except Exception:
                pass
            try:
                self._finalize_db_session(post_ctx, session_notes=None)
            except Exception:
                pass
        
    def pelletHandler(self, pim, roi):
        # events    0 - release pellet
        #           1 - load pellet
        #           2 - waiting to lose it
        if self.com.value < 0:
            return
        objDetected = False
        if pim > self.system_cfg['pelletThreshold']:
            objDetected = True
        if self.is_busy.value == -1:
            self.auto_pellet.SetValue(0)
            self.autoPellet(event=None)
            dlg = wx.MessageDialog(parent=None,message="Home position failed!",
                                   caption="Warning!", style=wx.OK|wx.ICON_EXCLAMATION)
            dlg.ShowModal()
            dlg.Destroy()
            return
        if self.is_busy.value == 0:
            getNewPellet = False
            if self.del_style.value == 0:
                wait2detect = 2
            else: 
                wait2detect = 2
                # objDetected = True
            if self.pellet_status == 0:
                print('send to mouse')
                self.com.value = 3
                while self.com.value > 0:
                    time.sleep(0.01)
                    
                self.pellet_timing = time.time()
                self.pellet_status = 1
            elif self.pellet_status == 1:
                if self.del_style.value == 0:
                    if objDetected:
                        self.hand_timing = time.time()
                        self.pellet_timing = time.time()
                        self.pellet_status = 2
                        self.failCt = 0
                        self.com.value = 6
                    elif (time.time()-self.pellet_timing) > wait2detect:
                        self.failCt+=1
                        if self.failCt > 3:
                            self.failCt = 0
                            beepList = [1,1,1]
                            self.auto_pellet.SetValue(0)
                            self.autoPellet(event=None)
                            self.pellet_timing = time.time()
                            self.pellet_status = 3
                            for d in beepList: 
                                duration = d  # seconds
                                freq = 940  # Hz
                                winsound.Beep(freq, duration)
                                time.sleep(d)
                        else:
                            getNewPellet = True
                else:
                    self.hand_timing = time.time()
                    self.pellet_timing = time.time()
                    self.pellet_status = 2
                    print("delay time")

            elif self.pellet_status ==  2:
                reveal_pellet = False
                if roi < self.system_cfg['handThreshold']:
                    if self.auto_delay.GetValue():
                        if int(self.delay_count.GetValue()) == 1:
                            delayval = int(self.tone_delay_min.GetValue())/1000
                        else:
                            delayval = self.delay_values[0]/1000
                        if self.first_delay == -1:
                            self.first_delay = self.delay_values[0]
                        if (time.time()-self.hand_timing) > delayval:
                            print('Delay %d complete' % self.delay_values[0])
                            self.delay_values = np.roll(self.delay_values, shift=-1)
                            if self.first_delay == self.delay_values[0]:
                                self.make_delay_iters()
                            reveal_pellet = True
                    elif (time.time()-self.hand_timing) > self.user_cfg['maxWait4Hand']:
                        reveal_pellet = True
                elif (time.time()-self.pellet_timing) > self.user_cfg['maxWait4Hand']:
                    getNewPellet = True
                else:
                    self.hand_timing = time.time()
                if reveal_pellet == True: # Reveal pellet
                    self.com.value = 4
                    if self.del_style.value == 1:
                        self.pellet_status = 4
                    else:
                        self.pellet_status = 3
                    self.pellet_timing = time.time()
                    self.delivery_delay = time.time()
                    if self.auto_stim.GetValue() and self.proto_str == 'First Reach':
                        self.stim_status.value = 1
                    print('revealing pellet')
                    
            elif self.pellet_status == 3: # Test whether to get new pellet
                if not objDetected:
                    if (time.time()-self.delivery_delay) > self.user_cfg['minTime2Eat']:
                        getNewPellet = True
                elif (time.time()-self.delivery_delay) > self.user_cfg['maxTime2Eat']:
                    getNewPellet = True
                    
            elif self.pellet_status == 4: #style B object detection listener
                if objDetected:
                    self.com.value = 6
                    self.pellet_status = 3
                elif (time.time()-self.pellet_timing) > wait2detect:
                    getNewPellet = True
                    
            if getNewPellet:
                if self.auto_stim.GetValue() and self.proto_str == 'First Reach':
                    self.stim_status.value = 0
                print('retrieving pellet')
                self.com.value = 2
                while self.com.value > 0:
                    time.sleep(0.01)
                self.pellet_status = 0
                self.pellet_timing = time.time()
            
    def vidPlayer(self, event):
        # Was the delivery style changed using the physical button?
        if not self.user_cfg['deliveryStyle'] == self.del_style.value:
            self.user_cfg['deliveryStyle'] = self.del_style.value
            self.setDelStyle()
            
        if self.camaq.value == 2:
            return
        # In simulation mode, synthesize dummy frames so GUI updates
        if self.simulate_mode:
            try:
                for ndx, im in enumerate(self.im):
                    # Create a simple gradient pattern that changes with time
                    t = int(time.time()*5) % 255
                    if ndx >= len(self.frame):
                        continue
                    self.frame[ndx][:] = (np.arange(self.frame[ndx].shape[0])[:,None] + np.arange(self.frame[ndx].shape[1])[None,:] + t) % 255
                    im.set_data(self.frame[ndx])
                self.figure.canvas.draw()
            except Exception:
                pass
            return
        for ndx, im in enumerate(self.im):
            if self.frmGrab[ndx].value == 1:
                self.frameBuff[ndx][0:] = np.frombuffer(self.array4feed[ndx].get_obj(), self.dtype, self.size)
                frame = self.frameBuff[ndx][0:self.dispSize[ndx]].reshape([self.aqH[ndx], self.aqW[ndx]])
                self.frame[ndx][self.y1[ndx]:self.y2[ndx],self.x1[ndx]:self.x2[ndx]] = frame
                im.set_data(self.frame[ndx])
                if not self.pellet_x == 0:
                    if not self.roi[0] == 0:
                        if self.inspect_stim.GetValue():
                            if self.system_cfg['stimAxes'] == self.camStrList[ndx]:
                                print('Stimulation ROI - %d' % np.mean(np.sum(frame,axis=0)[:5]))
                        if self.pelletAxes == self.axes[ndx]:
                            span = 6
                            cpt = np.asarray([self.pellet_x-span,span*2+1,self.pellet_y-span,span*2+1], int)
                            pim = self.frame[ndx][cpt[2]:cpt[2]+cpt[3],cpt[0]:cpt[0]+cpt[1]]
                            cpt = self.roi
                            roi = self.frame[ndx][cpt[2]:cpt[2]+cpt[3],cpt[0]:cpt[0]+cpt[1]]
                            if self.inspect_pellet.GetValue():
                                print('Pellet ROI - %d' % np.mean(pim[:]))
                            if self.inspect_hand.GetValue():
                                print('Hand ROI - %d' % np.mean(roi[:]))
                            
                            if self.auto_pellet.GetValue():
                                self.pelletHandler(np.mean(pim[:]),np.mean(roi[:]))
                                
                self.frmGrab[ndx].value = 0
                
        self.figure.canvas.draw()
        
        
    def autoCapture(self, event):
        self.sliderTabs+=self.sliderRate
        msg = '-'
        if (self.sliderTabs > self.slider.GetMax()) and not (msg == 'fail'):
            self.rec.SetValue(False)
            self.recordCam(event)
            self.slider.SetValue(0)
        else:
            self.slider.SetValue(round(self.sliderTabs))
            self.vidPlayer(event)
        
    def recordCam(self, event):
        if self.rec.GetValue():
            # Stop RFID listener for duration of recording
            try:
                if getattr(self, 'rfid_listening', False):
                    self._stop_rfid_listener()
            except Exception:
                pass
            # --- Pre-record dialog (only if RFID present) ---
            try:
                if getattr(self, 'mouse_meta', None) and not getattr(self, '_prerecord_context', None):
                    dlg = PreRecordDialog(self)
                    if dlg.ShowModal() == wx.ID_OK:
                        self._prerecord_context = dlg.get_values()
                    else:
                        dlg.Destroy()
                        self.rec.SetValue(False)
                        return
                    dlg.Destroy()
            except Exception as _e:
                try:
                    self.log.error('PreRecord dialog failed: %s', _e)
                except Exception:
                    pass
            self.compress_vid.Enable(False)
            self.system_cfg = clara.read_config()
            liveRate = 250
            self.Bind(wx.EVT_TIMER, self.autoCapture, self.recTimer)
            if int(self.minRec.GetValue()) == 0:
                return
            totTime = int(self.minRec.GetValue())*60
            
            for ndx, s in enumerate(self.camStrList):
                camID = str(self.system_cfg[s]['serial'])
                self.camq[camID].put('recordPrep')
                self.camq[camID].put('none')
                self.camq_p2read[camID].get()
            
            spaceneeded = 0
            for ndx, w in enumerate(self.aqW):
                recSize = w*self.aqH[ndx]*3*self.recSet[ndx]*totTime
                spaceneeded+=recSize
                
            self.slider.SetMax(100)
            self.slider.SetMin(0)
            self.slider.SetValue(0)
            self.sliderTabs = 0
            self.sliderRate = 100/(totTime/(liveRate/1000))
            
            date_string = datetime.datetime.now().strftime("%Y%m%d")
            base_dir = os.path.join(self.system_cfg['raw_data_dir'], date_string, self.system_cfg['unitRef'])
            if not os.path.exists(base_dir):
                os.makedirs(base_dir)
            freespace = shutil.disk_usage(base_dir)[2]
            if spaceneeded > freespace:
                dlg = wx.MessageDialog(parent=None,message="There is not enough disk space for the requested duration.",
                                       caption="Warning!", style=wx.OK|wx.ICON_EXCLAMATION)
                dlg.ShowModal()
                dlg.Destroy()
                self.rec.SetValue(False)
                return
            
            prev_expt_list = [name for name in os.listdir(base_dir) if name.startswith('session')]
            maxSess = 0;
            for p in prev_expt_list:
                sessNum = int(p[-3:])
                if sessNum > maxSess:
                    maxSess = sessNum
            comp_dir = os.path.join(self.system_cfg['interim_data_dir'], date_string, self.system_cfg['unitRef'])
            if os.path.exists(comp_dir):
                prev_expt_list = [name for name in os.listdir(comp_dir) if name.startswith('session')]
                for p in prev_expt_list:
                    sessNum = int(p[-3:])
                    if sessNum > maxSess:
                        maxSess = sessNum
            file_count = maxSess+1
            sess_string = '%s%03d' % ('session', file_count)
            self.sess_dir = os.path.join(base_dir, sess_string)
            if not os.path.exists(self.sess_dir):
                os.makedirs(self.sess_dir)
            self.meta,ruamelFile = clara.metadata_template()
            
            self.meta['duration (s)']=totTime
            self.meta['ID']=self.expt_id.GetValue()
            self.meta['placeholderA']='info'
            self.meta['placeholderB']='info'
            self.meta['Designer']='name'
            self.meta['Stim']=self.proto_str
            self.meta['StartTime']=datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            self.meta['Collection']='info'
            meta_name = '%s_%s_%s_metadata.yaml' % (date_string, self.system_cfg['unitRef'], sess_string)
            self.metapath = os.path.join(self.sess_dir,meta_name)
            usrdatadir = os.path.dirname(os.path.realpath(__file__))
            self.currUsr= self.user_drop.GetStringSelection()
            usrconfigname = os.path.join(usrdatadir,'Users', f'{self.currUsr}_userdata.yaml')
            sysconfigname = os.path.join(usrdatadir, 'systemdata.yaml')
            usrcopyname = '%s_%s_%s_%s_userdata_copy.yaml' % (date_string, self.system_cfg['unitRef'], sess_string, self.currUsr)
            syscopyname = '%s_%s_%s_systemdata_copy.yaml' % (date_string, self.system_cfg['unitRef'], sess_string)
            shutil.copyfile(usrconfigname,os.path.join(self.sess_dir,usrcopyname))
            shutil.copyfile(sysconfigname,os.path.join(self.sess_dir,syscopyname))
            # Update DB session paths now that they are known
            try:
                if self.save_to_db.GetValue() and getattr(self, '_active_session_id', None):
                    self._update_db_session_paths(self.sess_dir, self.metapath)
            except Exception:
                pass
            
            
            for ndx, s in enumerate(self.camStrList):
                camID = str(self.system_cfg[s]['serial'])
                name_base = '%s_%s_%s_%s' % (date_string, self.system_cfg['unitRef'], sess_string, self.system_cfg[s]['nickname'])
                path_base = os.path.join(self.sess_dir,name_base)
                self.camq[camID].put(path_base)
                self.camq_p2read[camID].get()
            
            if self.com.value >= 0:
                self.ardq.put('recordPrep')
                name_base = '%s_%s_%s' % (date_string, self.system_cfg['unitRef'], sess_string)
                path_base = os.path.join(self.sess_dir,name_base)
                self.ardq.put(path_base)
                self.ardq_p2read.get()
                
            for h in self.disable4cam:
                h.Enable(False)
            self.protocol.Enable(False)
            
            if not self.recTimer.IsRunning():
                if self.auto_pellet.GetValue():
                    if not self.pellet_x == 0:
                        if not self.roi[0] == 0:
                            self.pellet_timing = time.time()
                            self.hand_timing = time.time()
                            self.pellet_status = 3
                            self.delivery_delay = time.time()
                
                self.camaq.value = 1
                self.startAq()
                self.recTimer.Start(liveRate)
            self.rec.SetLabel('Stop')
            self.play.SetLabel('Abort')
            if getattr(self, 'mouse_meta', None):
                self.log.info('Recording started session_dir=%s rfid=%s', self.sess_dir, self.mouse_meta.get('rfid'))
            else:
                self.log.info('Recording started session_dir=%s no RFID metadata', self.sess_dir)
            # Write metalink entry if RFID metadata present
            try:
                if getattr(self, 'mouse_meta', None) and self.mouse_meta.get('rfid'):
                    self.write_metalink_entry()
            except Exception as e:
                self.log.exception('Failed to write metalink entry: %s', e)
        else:
            self.compress_vid.Enable(True)
            self.com.value = 11
            while self.com.value > 0:
                time.sleep(0.01)
            
            if self.com.value >= 0:
                self.ardq.put('Stop')
            
            self.meta['duration (s)']=round(self.meta['duration (s)']*(self.sliderTabs/100))
            # Inject RFID-derived mouse metadata if present
            if getattr(self, 'mouse_meta', None):
                try:
                    self.meta['mouse'] = self.mouse_meta
                    if 'rfid' in self.mouse_meta:
                        self.meta['RFID'] = self.mouse_meta['rfid']
                except Exception as e:
                    print(f'Failed to attach mouse metadata: {e}')
            clara.write_metadata(self.meta, self.metapath)
            self.log.info('Recording stopped session_dir=%s metadata_written=%s', self.sess_dir, self.metapath)
            # Restart RFID listener after recording (if not in live mode)
            try:
                if not self.play.GetValue():
                    self._start_rfid_listener()
            except Exception:
                pass
            # --- Post-record dialog ---
            post_context = None
            try:
                dlg2 = PostRecordDialog(self, getattr(self, '_prerecord_context', None))
                if dlg2.ShowModal() == wx.ID_OK:
                    post_context = dlg2.get_values()
                dlg2.Destroy()
            except Exception as _e:
                try:
                    self.log.error('PostRecord dialog failed: %s', _e)
                except Exception:
                    pass
            # Save combined metadata (JSON) in session dir
            try:
                import json, datetime as _dt, pathlib as _pl
                if hasattr(self, 'sess_dir') and os.path.isdir(self.sess_dir):
                    # Extended fields collection
                    try:
                        import platform as _pf, psutil as _ps
                    except Exception:
                        _pf = None; _ps = None
                    # System / environment snapshot
                    env_info = {}
                    try:
                        import sys as _sys
                        env_info['python_version'] = _sys.version.split()[0]
                        if _pf:
                            env_info['platform'] = _pf.platform()
                            env_info['machine'] = _pf.machine()
                            env_info['processor'] = _pf.processor()
                        if _ps:
                            env_info['cpu_percent'] = _ps.cpu_percent(interval=0.1)
                            env_info['virtual_memory'] = dict(total=_ps.virtual_memory().total, available=_ps.virtual_memory().available)
                    except Exception:
                        pass
                    # Video file inventory (augment with hashes & sizes)
                    video_files = []
                    try:
                        import hashlib as _hash
                        for f in os.scandir(self.sess_dir):
                            if f.is_file() and f.name.lower().endswith(('.avi', '.mp4', '.mkv', '.mov')):
                                h = None
                                try:
                                    with open(f.path, 'rb') as _vf:
                                        chunk = _vf.read(1024 * 1024)
                                        dig = _hash.sha256()
                                        dig.update(chunk)
                                        h = dig.hexdigest()[:16]
                                except Exception:
                                    pass
                                video_files.append({'name': f.name, 'size_bytes': f.stat().st_size, 'sha256_prefix': h})
                    except Exception:
                        pass
                    # Camera configuration snapshot
                    cam_cfg = []
                    try:
                        for idx, cam_key in enumerate(self.camStrList):
                            cam_cfg.append({
                                'key': cam_key,
                                'serial': self.system_cfg.get(cam_key, {}).get('serial'),
                                'nickname': self.system_cfg.get(cam_key, {}).get('nickname'),
                                'ismaster': self.system_cfg.get(cam_key, {}).get('ismaster'),
                                'crop_roi': self.croproi[idx] if idx < len(self.croproi) else None,
                            })
                    except Exception:
                        pass
                    # Stim events placeholder (could be populated elsewhere later)
                    stim_events = getattr(self, '_stim_events', [])
                    merged = {
                        'timestamp': _dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
                        'rfid': getattr(self.mouse_meta, 'rfid', None) if isinstance(getattr(self, 'mouse_meta', None), dict) else (self.mouse_meta.get('rfid') if getattr(self, 'mouse_meta', None) else None),
                        'animal_meta': getattr(self, 'mouse_meta', None),
                        'prerecord': getattr(self, '_prerecord_context', None),
                        'postrecord': post_context,
                        'duration_seconds': self.meta.get('duration (s)'),
                        'stim_protocol': getattr(self, 'proto_str', None),
                        'camera_config': cam_cfg,
                        'environment': env_info,
                        'video_files': video_files,
                        'stim_events': stim_events,
                    }
                    jf = _pl.Path(self.sess_dir) / 'session_notes.json'
                    with jf.open('w', encoding='utf-8') as fh:
                        json.dump(merged, fh, indent=2)
                    # Finalize DB session now (if enabled)
                    try:
                        if self.save_to_db.GetValue() and getattr(self, '_active_session_id', None):
                            self._finalize_db_session(post_context, session_notes=merged)
                    except Exception:
                        pass
            except Exception as _e:
                try:
                    self.log.error('Failed writing session_notes.json: %s', _e)
                except Exception:
                    pass
            # Prompt for new subject if RFID present
            try:
                if getattr(self, 'mouse_meta', None) and self.mouse_meta.get('rfid'):
                    q = wx.MessageDialog(self, 'Will you switch to a new subject next?', 'New Subject?', style=wx.YES_NO|wx.ICON_QUESTION)
                    if q.ShowModal() == wx.ID_YES:
                        # Clear RFID state
                        try:
                            self.mouse_meta = None
                            self.active_rfid = None
                            if hasattr(self, 'rfid_input'):
                                self.rfid_input.SetValue('')
                            self.statusbar.SetStatusText('RFID cleared for next subject')
                        except Exception:
                            pass
                    q.Destroy()
                # Reset prerecord context regardless
                if hasattr(self, '_prerecord_context'):
                    delattr(self, '_prerecord_context')
            except Exception:
                pass
            if self.recTimer.IsRunning():
                self.recTimer.Stop()
            self.stopAq()
            time.sleep(2)
            
            ok2move = False
            try:
                if not self.mv.is_alive():
                    self.mv.terminate()   
                    ok2move = True
            except:
                ok2move = True
            if self.play == event.GetEventObject():
                ok2move = False
            if ok2move:
                self.mv = clara.moveVids()
                self.mv.start()
            
            self.slider.SetValue(0)
            self.rec.SetLabel('Record')
            self.play.SetLabel('Play')
            self.protocol.Enable(True)
            for h in self.disable4cam:
                h.Enable(True)
    
    def initThreads(self):
        if self.simulate_mode:
            # Already in simulation; nothing to init
            return True
        try:
            self.camq = dict()
            self.camq_p2read = dict()
            self.cam = list()
            for ndx, camID in enumerate(self.camIDlsit):
                self.camq[camID] = Queue()
                self.camq_p2read[camID] = Queue()
                self.cam.append(spin.multiCam_DLC_Cam(self.camq[camID], self.camq_p2read[camID],
                                                   camID, self.camIDlsit,
                                                   self.frmDims, self.camaq,
                                                   self.frmaq, self.array4feed[ndx], self.frmGrab[ndx],
                                                   self.com, self.stim_status))
                self.cam[ndx].start()
            # Initialize master then slaves; use timeouts to avoid blocking forever
            for m in self.mlist:
                try:
                    self.camq[m].put('InitM')
                    self.camq_p2read[m].get(timeout=5)
                except Exception:
                    raise RuntimeError(f'Camera init (master {m}) failed')
            for s in self.slist:
                try:
                    self.camq[s].put('InitS')
                    self.camq_p2read[s].get(timeout=5)
                except Exception:
                    raise RuntimeError(f'Camera init (slave {s}) failed')
            # Arduino thread (optional)
            self.ardq = Queue()
            self.ardq_p2read = Queue()
            self.ard = arduino.arduinoCtrl(self.ardq, self.ardq_p2read, self.frmaq, self.com,
                                           self.is_busy, self.mVal, self.stim_status, self.stim_selection, self.del_style)
            self.ard.start()
            try:
                self.ardq_p2read.get(timeout=5)
            except Exception:
                self.log.warning('Arduino init timeout; continuing without Arduino')
                self.com.value = -1
            return True
        except Exception as e:
            # Fallback to simulation mode
            try:
                self.log.warning('Camera initialization failed (%s); enabling simulation mode', e)
            except Exception:
                pass
            try:
                self.init_errors.append(f'Camera init failure: {e}')
            except Exception:
                pass
            self.simulate_mode = True
            # Clean up any partially started processes
            try:
                for proc in getattr(self, 'cam', []):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            except Exception:
                pass
            # Ensure Arduino disabled
            self.com.value = -1
            return False
        
    def deinitThreads(self):
        if self.simulate_mode:
            return
        try:
            for n, camID in enumerate(self.camIDlsit):
                self.camq[camID].put('Release')
                try:
                    self.camq_p2read[camID].get(timeout=2)
                except Exception:
                    pass
                try:
                    self.camq[camID].close(); self.camq_p2read[camID].close()
                except Exception:
                    pass
                try:
                    self.cam[n].terminate()
                except Exception:
                    pass
            if self.com.value >= 0:
                try:
                    self.ardq.put('Release')
                    self.ardq_p2read.get(timeout=2)
                except Exception:
                    pass
                try:
                    self.ardq.close(); self.ardq_p2read.close(); self.ard.terminate()
                except Exception:
                    pass
        except Exception:
            pass
            
    def startAq(self):
        if self.simulate_mode:
            return
        for m in self.mlist:
            self.camq[m].put('Start')
        for s in self.slist:
            self.camq[s].put('Start')
        for m in self.mlist:
            self.camq[m].put('TrigOff')
        
    def stopAq(self):
        if self.simulate_mode:
            self.camaq.value = 0
            return
        self.camaq.value = 0
        for s in self.slist:
            self.camq[s].put('Stop')
            try:
                self.camq_p2read[s].get(timeout=3)
            except Exception:
                pass
        for m in self.mlist:
            self.camq[m].put('Stop')
            try:
                self.camq_p2read[m].get(timeout=3)
            except Exception:
                pass
        
    def updateSettings(self, event):
        self.system_cfg = clara.read_config()
        # Simulation: fabricate camera settings
        if self.simulate_mode:
            self.aqW = [self.frmDims[3]] * len(self.camIDlsit)
            self.aqH = [self.frmDims[1]] * len(self.camIDlsit)
            self.recSet = [30] * len(self.camIDlsit)
            print('Simulation mode: using dummy camera settings (30fps)')
            # Ensure overlays exist
            self._add_simulation_overlays()
            return
        self.aqW = list()
        self.aqH = list()
        self.recSet = list()
        for n, camID in enumerate(self.camIDlsit):
            try:
                self.camq[camID].put('updateSettings')
                self.camq_p2read[camID].get(timeout=2)
                if self.auto_stim.GetValue():
                    self.camq[camID].put('roi')
                elif self.crop.GetValue():
                    self.camq[camID].put('crop')
                else:
                    self.camq[camID].put('full')
                self.recSet.append(self.camq_p2read[camID].get(timeout=5))
                aqW = self.camq_p2read[camID].get(timeout=2)
                self.aqW.append(int(aqW))
                aqH = self.camq_p2read[camID].get(timeout=2)
                self.aqH.append(int(aqH))
            except Exception as e:
                print('\nCamera settings update failed (%s). Enabling simulation mode.\n' % e)
                try:
                    self.init_errors.append(f'updateSettings failure cam {camID}: {e}')
                except Exception:
                    pass
                self.simulate_mode = True
                self.aqW = [self.frmDims[3]] * len(self.camIDlsit)
                self.aqH = [self.frmDims[1]] * len(self.camIDlsit)
                self.recSet = [30] * len(self.camIDlsit)
                self._add_simulation_overlays()
                break
            print('frame rate ' + self.camStrList[n] + ' : ' + str(round(self.recSet[n])))
                
    def initCams(self, event):
        if self.init.GetValue() == True:
            self.Enable(False)
            success = self.initThreads()
            if not success:
                self.simulate_mode = True
            self.updateSettings(event)
            
            self.Bind(wx.EVT_TIMER, self.vidPlayer, self.liveTimer)
            
            self.camaq.value = 1
            if not self.simulate_mode:
                self.startAq()
                time.sleep(1)
                self.camaq.value = 0
                self.stopAq()
            self.x1 = list()
            self.x2 = list()
            self.y1 = list()
            self.y2 = list()
            self.h = list()
            self.w = list()
            self.dispSize = list()
            for ndx, im in enumerate(self.im):
                self.frame[ndx] = np.zeros(self.shape, dtype='ubyte')
                if not self.simulate_mode:
                    self.frameBuff[ndx][0:] = np.frombuffer(self.array4feed[ndx].get_obj(), self.dtype, self.size)
                if self.auto_stim.GetValue() and self.stimAxes == self.axes[ndx]:
                    self.h.append(self.stimroi[3])
                    self.w.append(self.stimroi[1])
                    self.y1.append(self.stimroi[2])
                    self.x1.append(self.stimroi[0])
                    self.set_stim.Enable(False)
                    self.set_crop.Enable(False)
                    self.inspect_stim.Enable(True)
                elif self.crop.GetValue():
                    self.h.append(self.croproi[ndx][3])
                    self.w.append(self.croproi[ndx][1])
                    self.y1.append(self.croproi[ndx][2])
                    self.x1.append(self.croproi[ndx][0])
                    self.set_crop.Enable(False)
                    self.set_stim.Enable(True)
                    self.inspect_stim.Enable(False)
                else:
                    self.h.append(self.frmDims[1])
                    self.w.append(self.frmDims[3])
                    self.y1.append(self.frmDims[0])
                    self.x1.append(self.frmDims[2])
                    self.set_crop.Enable(True)
                    self.set_stim.Enable(True)
                    self.inspect_stim.Enable(False)
                
                self.dispSize.append(self.aqH[ndx]*self.aqW[ndx])
                self.y2.append(self.y1[ndx]+self.aqH[ndx])
                self.x2.append(self.x1[ndx]+self.aqW[ndx])
                # Populate initial image data
                if self.simulate_mode:
                    # Draw a gradient frame; then copy only the valid intersection into ROI area.
                    sim_frame = np.add.outer(np.arange(self.aqH[ndx])%255,
                                             np.arange(self.aqW[ndx])%255).astype('uint8')
                    y1, y2 = self.y1[ndx], self.y2[ndx]
                    x1, x2 = self.x1[ndx], self.x2[ndx]
                    maxH, maxW = self.frame[ndx].shape
                    # Clip to actual frame bounds (important when ROI origin + aq dims extend past edge)
                    if y2 > maxH:
                        y2 = maxH
                    if x2 > maxW:
                        x2 = maxW
                    eff_h = max(0, y2 - y1)
                    eff_w = max(0, x2 - x1)
                    if eff_h > 0 and eff_w > 0:
                        self.frame[ndx][y1:y2, x1:x2] = sim_frame[:eff_h, :eff_w]
                else:
                    frame = self.frameBuff[ndx][0:self.dispSize[ndx]].reshape([self.aqH[ndx], self.aqW[ndx]])
                    self.frame[ndx][self.y1[ndx]:self.y2[ndx],self.x1[ndx]:self.x2[ndx]] = frame
                im.set_data(self.frame[ndx])
                
                    
                if not self.croproi[ndx][0] == 0:
                    self.croprec[ndx].set_alpha(0.6)

                if not self.pellet_x == 0:
                    if not self.roi[0] == 0:
                        if self.pelletAxes == self.axes[ndx]:
                            self.pLoc[ndx].set_alpha(0.6)
                            self.roirec[ndx].set_alpha(0.6)

                if not self.stimroi[0] == 0:
                    if self.stimAxes == self.axes[ndx]:
                        self.stimrec[ndx].set_alpha(0.6)
            
            self.init.SetLabel('Release')
            self.crop.Enable(False)
            self.auto_stim.Enable(False)
            self.auto_pellet.Enable(True)
            if self.simulate_mode:
                self.statusbar.SetStatusText('Simulation mode (hardware not initialized)')
                self._add_simulation_overlays()
            
            for h in self.onWhenCamEnabled:
                h.Enable(True)
            
            if (not self.com.value < 0) and (not self.simulate_mode):
                if self.auto_delay.GetValue():
                    self.make_delay_iters()
                self.setProtocol(None)
                self.setDelStyle()
                self.com.value = 7 # block ButtonStyleChange
                while self.com.value > 0:
                    time.sleep(0.01)
                self.com.value = 12 # set X position
                while self.com.value > 0:
                    time.sleep(0.01)
                self.com.value = 13 # set Y position
                while self.com.value > 0:
                    time.sleep(0.01)
                self.com.value = 14 # set Z position
                while self.com.value > 0:
                    time.sleep(0.01)
                self.com.value = 1 # send home
                while self.com.value > 0:
                    time.sleep(0.01)
                    
                
                for h in self.serHlist:
                    h.Enable(True)
            
            self.Enable(True)
            # Attempt automatic RFID listener start (even in simulation mode) after successful GUI init
            self.log.info('Starting RFID listener (if configured)')
            self._start_rfid_listener()
            self.figure.canvas.draw()
        else:
            if not self.com.value < 0:
                self.com.value = 8 # allowButtonStyleChange
                while self.com.value > 0:
                    time.sleep(0.01)
                    
            if self.play.GetValue():
                self.play.SetValue(False)
                self.liveFeed(event)
            if self.rec.GetValue():
                self.rec.SetValue(False)
                self.recordCam(event)
            self.init.SetLabel('Enable')
            for h in self.serHlist:
                h.Enable(False)
            for ndx, im in enumerate(self.im):
                self.frame[ndx] = np.zeros(self.shape, dtype='ubyte')
                im.set_data(self.frame[ndx])
                self.croprec[ndx].set_alpha(0)
                self.pLoc[ndx].set_alpha(0)
                self.roirec[ndx].set_alpha(0)
                self.stimrec[ndx].set_alpha(0)
            self.figure.canvas.draw()
            
            self.crop.Enable(True)
            if not self.stimAxes == None:
                self.auto_stim.Enable(True)
            self.set_crop.Enable(False)
            self.set_stim.Enable(False)
            self.auto_pellet.Enable(False)
            self.inspect_stim.Enable(False)
            for h in self.onWhenCamEnabled:
                h.Enable(False)
            
            if not self.simulate_mode:
                self.deinitThreads()
            # Stop RFID listener when releasing acquisition resources
            try:
                if getattr(self, 'rfid_listening', False):
                    self._stop_rfid_listener()
            except Exception:
                pass

    def _add_simulation_overlays(self, initial: bool=False):
        """Overlay SIM MODE label on each camera axes (idempotent)."""
        if not hasattr(self, 'axes'):
            return
        try:
            if not hasattr(self, '_sim_overlay_artists'):
                self._sim_overlay_artists = []
            if self._sim_overlay_artists:
                # already added
                return
            for ax in self.axes:
                txt = ax.text(0.5, 0.5, 'SIMULATION\nMODE', color='red', fontsize=22,
                              ha='center', va='center', alpha=0.35, transform=ax.transAxes,
                              fontweight='bold')
                self._sim_overlay_artists.append(txt)
            self.figure.canvas.draw_idle()
        except Exception:
            pass

    def showHardwareStatus(self, event):
        """Popup summarizing hardware state and troubleshooting tips."""
        sim = 'ENABLED' if self.simulate_mode else 'OFF'
        tips = [
            'Check camera USB/PCIe connections and power.',
            'Verify PySpin SDK installed and matches camera driver.',
            'Close other apps using the cameras.',
            'Confirm systemdata.yaml serial numbers match actual cameras.',
            'Arduino: check correct COM port in systemdata.yaml (key COM).',
            'Run with --simulate to bypass hardware while testing GUI.'
        ]
        err_lines = '\n'.join(self.init_errors[-10:]) if self.init_errors else 'None recorded.'
        msg = f'Hardware Status:\n  Simulation: {sim}\n  Cameras: {len(getattr(self, "camStrList", []))} configured\n  Arduino COM active: {"Yes" if self.com.value >= 0 and not self.simulate_mode else "No"}\n\nRecent init errors:\n{err_lines}\n\nTroubleshooting tips:\n - ' + '\n - '.join(tips)
        dlg = wx.MessageDialog(self, msg, 'Hardware Status', style=wx.OK|wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()
        
    def quitButton(self, event):
        """
        Quits the GUI
        """
        print('Close event called')
        # Stop background processes we introduced
        try:
            if getattr(self, 'rfid_listening', False):
                self._stop_rfid_listener()
        except Exception:
            pass
        try:
            if getattr(self, 'animal_export_active', False):
                if self.animal_export_proc.is_alive():
                    self.animal_export_proc.terminate()
        except Exception:
            pass
        if self.play.GetValue():
            self.play.SetValue(False)
            self.liveFeed(event)
        if self.rec.GetValue():
            self.rec.SetValue(False)
            self.recordCam(event)
        if self.init.GetValue():
            self.init.SetValue(False)
            self.initCams(event)
        
        try:
            if not self.mv.is_alive():
                self.mv.terminate()
            else:
                print('File transfer in progress...\n')
                print('Do not record again until transfer completes.\n')
        except:
            pass
        
        try:
            if self.compressThread.is_alive():
                dlg = wx.MessageDialog(parent=None,message="Pausing until previous compression completes!",
                                       caption="Warning!", style=wx.OK|wx.ICON_EXCLAMATION)
                dlg.ShowModal()
                dlg.Destroy()
                while self.compressThread.is_alive():
                    time.sleep(10)
            
            self.compressThread.terminate()   
        except:
            pass
        
        self.statusbar.SetStatusText("")
        self.Destroy()
    
def show(argv=None):
    """Launch GUI. Optional argv for testability."""
    import argparse, sys as _sys
    if argv is None:
        argv = _sys.argv[1:]
    parser = argparse.ArgumentParser(description='RT Video Acquisition GUI')
    parser.add_argument('--simulate', action='store_true', help='Start in simulation mode (no hardware access)')
    args, _unknown = parser.parse_known_args(argv)
    app = wx.App()
    MainFrame(None, simulate=args.simulate).Show()
    app.MainLoop()

if __name__ == '__main__':
    show()

# ---------------- New Dialog Classes (Pre/Post Recording) -----------------

class PreRecordDialog(wx.Dialog):
    """Collect pre-recording context (recording type, modality, task, notes)."""
    def __init__(self, parent):
        super().__init__(parent, title='Pre-Recording Details', style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER)
        pnl = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 6, 8)
        grid.Add(wx.StaticText(pnl, label='Recording Type:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.choice_record_type = wx.Choice(pnl, choices=['Behavior', 'Imaging', 'Calibration', 'Other'])
        self.choice_record_type.SetSelection(0)
        grid.Add(self.choice_record_type, 1, wx.EXPAND)
        grid.Add(wx.StaticText(pnl, label='Imaging Modality:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.choice_modality = wx.Choice(pnl, choices=['None', 'Widefield', '2P', 'Miniscope', 'Other'])
        self.choice_modality.SetSelection(0)
        grid.Add(self.choice_modality, 1, wx.EXPAND)
        grid.Add(wx.StaticText(pnl, label='Task / Protocol:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_task = wx.TextCtrl(pnl, value='')
        grid.Add(self.txt_task, 1, wx.EXPAND)
        grid.AddGrowableCol(1, 1)
        vbox.Add(grid, 0, wx.ALL|wx.EXPAND, 10)
        vbox.Add(wx.StaticText(pnl, label='Pre-Recording Notes:'), 0, wx.LEFT|wx.RIGHT|wx.TOP, 10)
        self.txt_notes = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1,120))
        vbox.Add(self.txt_notes, 1, wx.ALL|wx.EXPAND, 10)
        btns = self.CreateSeparatedButtonSizer(wx.OK|wx.CANCEL)
        vbox.Add(btns, 0, wx.ALL|wx.EXPAND, 8)
        pnl.SetSizer(vbox)
        self.SetSizerAndFit(vbox)
        self.SetMinSize((450, 380))

    def get_values(self):
        return {
            'recording_type': self.choice_record_type.GetStringSelection(),
            'modality': self.choice_modality.GetStringSelection(),
            'task_protocol': self.txt_task.GetValue().strip(),
            'pre_notes': self.txt_notes.GetValue().strip(),
        }


class PostRecordDialog(wx.Dialog):
    """Collect post-recording outcomes and notes."""
    def __init__(self, parent, prerecord_context=None):
        super().__init__(parent, title='Post-Recording Details', style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER)
        pnl = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        if prerecord_context:
            info_str = f"Type: {prerecord_context.get('recording_type','')}  Modality: {prerecord_context.get('modality','')}  Task: {prerecord_context.get('task_protocol','')}"
            vbox.Add(wx.StaticText(pnl, label=info_str), 0, wx.ALL, 10)
        grid = wx.FlexGridSizer(0, 2, 6, 8)
        grid.Add(wx.StaticText(pnl, label='Outcome:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.choice_outcome = wx.Choice(pnl, choices=['Completed', 'Aborted Early', 'Technical Issue', 'Other'])
        self.choice_outcome.SetSelection(0)
        grid.Add(self.choice_outcome, 1, wx.EXPAND)
        grid.Add(wx.StaticText(pnl, label='Data Quality:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.choice_quality = wx.Choice(pnl, choices=['Good', 'Fair', 'Poor', 'Unknown'])
        self.choice_quality.SetSelection(0)
        grid.Add(self.choice_quality, 1, wx.EXPAND)
        grid.AddGrowableCol(1, 1)
        vbox.Add(grid, 0, wx.ALL|wx.EXPAND, 10)
        vbox.Add(wx.StaticText(pnl, label='Post-Recording Notes:'), 0, wx.LEFT|wx.RIGHT|wx.TOP, 10)
        self.txt_notes = wx.TextCtrl(pnl, style=wx.TE_MULTILINE, size=(-1,140))
        vbox.Add(self.txt_notes, 1, wx.ALL|wx.EXPAND, 10)
        btns = self.CreateSeparatedButtonSizer(wx.OK|wx.CANCEL)
        vbox.Add(btns, 0, wx.ALL|wx.EXPAND, 8)
        pnl.SetSizer(vbox)
        self.SetSizerAndFit(vbox)
        self.SetMinSize((460, 360))

    def get_values(self):
        return {
            'outcome': self.choice_outcome.GetStringSelection(),
            'quality': self.choice_quality.GetStringSelection(),
            'post_notes': self.txt_notes.GetValue().strip(),
        }


class SessionHistoryDialog(wx.Dialog):
    """Display recent sessions for the current RFID with basic details and JSON preview."""
    def __init__(self, parent, rfid: str, sessions: list):
        super().__init__(parent, title=f'Session History - RFID {rfid}', style=wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER)
        self.sessions = sessions
        pnl = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.list_ctrl = wx.ListCtrl(pnl, style=wx.LC_REPORT|wx.BORDER_SUNKEN)
        cols = ['Start (UTC)', 'Stop (UTC)', 'Type', 'Modality', 'Task', 'Outcome', 'Quality', 'LiveOnly', 'Dir']
        for i, c in enumerate(cols):
            self.list_ctrl.InsertColumn(i, c)
        for sess in sessions:
            prerec = (sess.get('prerecord') or {})
            postrec = (sess.get('postrecord') or {})
            idx = self.list_ctrl.InsertItem(self.list_ctrl.GetItemCount(), sess.get('start_utc',''))
            self.list_ctrl.SetItem(idx, 1, sess.get('stop_utc',''))
            self.list_ctrl.SetItem(idx, 2, prerec.get('recording_type',''))
            self.list_ctrl.SetItem(idx, 3, prerec.get('modality',''))
            self.list_ctrl.SetItem(idx, 4, prerec.get('task_protocol',''))
            self.list_ctrl.SetItem(idx, 5, postrec.get('outcome',''))
            self.list_ctrl.SetItem(idx, 6, postrec.get('quality',''))
            self.list_ctrl.SetItem(idx, 7, 'Y' if sess.get('was_live_only') else '')
            self.list_ctrl.SetItem(idx, 8, os.path.basename(sess.get('session_dir') or '') )
            self.list_ctrl.SetItemData(idx, idx)
        for i in range(len(cols)):
            self.list_ctrl.SetColumnWidth(i, wx.LIST_AUTOSIZE_USEHEADER)
        vbox.Add(self.list_ctrl, 1, wx.ALL|wx.EXPAND, 8)
        self.json_preview = wx.TextCtrl(pnl, style=wx.TE_MULTILINE|wx.TE_READONLY)
        vbox.Add(self.json_preview, 1, wx.ALL|wx.EXPAND, 8)
        btns = self.CreateSeparatedButtonSizer(wx.OK)
        vbox.Add(btns, 0, wx.ALL|wx.EXPAND, 5)
        pnl.SetSizer(vbox)
        self.SetSizerAndFit(vbox)
        self.SetMinSize((900, 500))
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onSelect)

    def onSelect(self, event):
        idx = event.GetIndex()
        if idx < 0 or idx >= len(self.sessions):
            return
        import json
        try:
            txt = json.dumps(self.sessions[idx].get('session_notes') or {}, indent=2)
        except Exception:
            txt = '(invalid JSON)'
        self.json_preview.SetValue(txt)
