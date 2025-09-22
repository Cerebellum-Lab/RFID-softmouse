"""Configuration dialog classes for SoftMouse and RFID settings.

Separated from the main GUI file to simplify maintenance and avoid
indentation/scope errors in the very large MainFrame module.
"""
from __future__ import annotations

import wx


class SoftMouseConfigDialog(wx.Dialog):
    """Dialog for configuring SoftMouse export / login behavior."""

    def __init__(self, parent):
        super().__init__(parent, title='SoftMouse Configuration', style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent = parent
        pnl = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 5, 5)

        # Colony name
        grid.Add(wx.StaticText(pnl, label='Colony Name:'), 0, wx.ALIGN_CENTER_VERTICAL)
        colony_val = getattr(parent, '_softmouse_last_colony', '') or ''
        self.txt_colony = wx.TextCtrl(pnl, value=colony_val)
        grid.Add(self.txt_colony, 1, wx.EXPAND)

        # Fast / Headful
        grid.Add(wx.StaticText(pnl, label='Fast Animals:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_fast = wx.CheckBox(pnl)
        self.chk_fast.SetValue(bool(getattr(parent, '_softmouse_fast_flag', False)))
        grid.Add(self.chk_fast)

        grid.Add(wx.StaticText(pnl, label='Headful Browser:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_headful = wx.CheckBox(pnl)
        self.chk_headful.SetValue(bool(getattr(parent, '_softmouse_headful_flag', False)))
        grid.Add(self.chk_headful)

        # Force login, Save state, Parse
        self.chk_force_login = wx.CheckBox(pnl, label='Force fresh login')
        grid.Add(self.chk_force_login, 0, wx.ALIGN_LEFT)
        self.chk_save_state = wx.CheckBox(pnl, label='Save state after login')
        grid.Add(self.chk_save_state, 0, wx.ALIGN_LEFT)
        self.chk_parse = wx.CheckBox(pnl, label='Parse exported file')
        grid.Add(self.chk_parse, 0, wx.ALIGN_LEFT)
        # Initialize flag checkboxes from parent attributes if present
        self.chk_force_login.SetValue(bool(getattr(parent, '_softmouse_force_login', False)))
        self.chk_save_state.SetValue(bool(getattr(parent, '_softmouse_save_state', False)))
        self.chk_parse.SetValue(bool(getattr(parent, '_softmouse_parse', True)))

        # Spacer
        grid.Add((5, 5))

        # Buttons row
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_test_login = wx.Button(pnl, label='Test Login Only')
        btn_sizer.Add(self.btn_test_login, 0, wx.RIGHT, 8)
        self.btn_run_export = wx.Button(pnl, label='Run Export')
        btn_sizer.Add(self.btn_run_export, 0, wx.RIGHT, 8)
        self.btn_close = wx.Button(pnl, id=wx.ID_CLOSE, label='Close')
        btn_sizer.Add(self.btn_close, 0)

        sizer.Add(grid, 1, wx.ALL | wx.EXPAND, 10)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        pnl.SetSizerAndFit(sizer)
        self.SetSizerAndFit(sizer)

        # Bindings
        self.btn_close.Bind(wx.EVT_BUTTON, self.onClose)
        self.btn_test_login.Bind(wx.EVT_BUTTON, self.onTestLogin)
        self.btn_run_export.Bind(wx.EVT_BUTTON, self.onRunExport)

    # ---------------- Internal helpers -----------------
    def onClose(self, event):  # noqa: D401
        self.EndModal(wx.ID_OK)

    def _persist_to_parent(self):
        # Mirror dialog values into parent legacy widgets/attributes if they exist
        # Store flags & values as attributes (persisted to YAML by parent.write_user_config)
        self.parent._softmouse_last_colony = self.txt_colony.GetValue().strip()
        self.parent._softmouse_fast_flag = self.chk_fast.GetValue()
        self.parent._softmouse_headful_flag = self.chk_headful.GetValue()
        self.parent._softmouse_force_login = self.chk_force_login.GetValue()
        self.parent._softmouse_save_state = self.chk_save_state.GetValue()
        self.parent._softmouse_parse = self.chk_parse.GetValue()
        if hasattr(self.parent, 'write_user_config'):
            try:
                self.parent.write_user_config()
            except Exception:
                pass
        if hasattr(self.parent, 'statusbar'):
            self.parent.statusbar.SetStatusText('SoftMouse config updated')

    def onTestLogin(self, event):  # noqa: D401
        self._persist_to_parent()
        colony = self.txt_colony.GetValue().strip()
        if not colony:
            if hasattr(self.parent, 'statusbar'):
                self.parent.statusbar.SetStatusText('Enter colony name before test login')
            return
        # Launch login-only run via export_runner
        try:
            import multiprocessing as mp
            from automation.export_runner import run_export
        except Exception as e:
            if hasattr(self.parent, 'statusbar'):
                self.parent.statusbar.SetStatusText(f'Login test import error: {e}')
            return
        if getattr(self.parent, 'login_test_active', False):
            if hasattr(self.parent, 'statusbar'):
                self.parent.statusbar.SetStatusText('Login test already running')
            return
        q = mp.Queue()
        fast = bool(getattr(self.parent, '_softmouse_fast_flag', False))
        headful = bool(getattr(self.parent, '_softmouse_headful_flag', False))
        force_login = bool(getattr(self.parent, '_softmouse_force_login', False))
        save_state = bool(getattr(self.parent, '_softmouse_save_state', False))
        proc = mp.Process(target=run_export, args=(
            colony,
            fast,
            headful,
            'softmouse_storage_state.json',
            'downloads_animals',
            q,
            True,  # login_only
            force_login,
            save_state,
        ), daemon=True)
        try:
            proc.start()
        except Exception as e:
            if hasattr(self.parent, 'statusbar'):
                self.parent.statusbar.SetStatusText(f'Login test start error: {e}')
            return
        self.parent.login_test_active = True
        self.parent.login_test_queue = q
        self.parent.login_test_proc = proc
        if not hasattr(self.parent, 'backgroundTimer'):
            import wx as _wx
            self.parent.backgroundTimer = _wx.Timer(self.parent)
            self.parent.Bind(_wx.EVT_TIMER, self.parent.pollBackground, self.parent.backgroundTimer)
        if not self.parent.backgroundTimer.IsRunning():
            self.parent.backgroundTimer.Start(500)
        if hasattr(self.parent, 'statusbar'):
            self.parent.statusbar.SetStatusText('SoftMouse login test started...')

    def onRunExport(self, event):  # noqa: D401
        self._persist_to_parent()
        # Delegate to parent existing method
        if hasattr(self.parent, 'startAnimalExport'):
            self.parent.startAnimalExport(None)


class RFIDConfigDialog(wx.Dialog):
    """Dialog for configuring RFID serial listener parameters."""

    def __init__(self, parent):
        super().__init__(parent, title='RFID Configuration', style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.parent = parent
        pnl = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 5, 5)

        grid.Add(wx.StaticText(pnl, label='RFID Port:'), 0, wx.ALIGN_CENTER_VERTICAL)
        port_val = ''
        if hasattr(parent, 'rfid_port') and isinstance(parent.rfid_port, wx.TextCtrl):
            try:
                port_val = parent.rfid_port.GetValue()
            except Exception:
                port_val = ''
        self.txt_port = wx.TextCtrl(pnl, value=port_val or 'COM3')
        grid.Add(self.txt_port, 1, wx.EXPAND)

        grid.Add(wx.StaticText(pnl, label='Baud Rate:'), 0, wx.ALIGN_CENTER_VERTICAL)
        baud_val = 9600
        if hasattr(parent, 'rfid_baud'):
            try:
                baud_val = int(parent.rfid_baud.GetValue())
            except Exception:
                baud_val = 9600
        self.spin_baud = wx.SpinCtrl(pnl, value=str(baud_val), min=1200, max=115200)
        grid.Add(self.spin_baud)

        self.chk_autostart = wx.CheckBox(pnl, label='Auto-start listener')
        grid.Add(self.chk_autostart, 0, wx.ALIGN_LEFT)

        # Spacer
        grid.Add((5, 5))

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_apply = wx.Button(pnl, wx.ID_APPLY, 'Apply')
        btn_sizer.Add(self.btn_apply, 0, wx.RIGHT, 8)
        self.btn_close = wx.Button(pnl, wx.ID_CLOSE, 'Close')
        btn_sizer.Add(self.btn_close, 0)

        sizer.Add(grid, 1, wx.ALL | wx.EXPAND, 10)
        sizer.Add(btn_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        pnl.SetSizerAndFit(sizer)
        self.SetSizerAndFit(sizer)

        self.btn_close.Bind(wx.EVT_BUTTON, self.onClose)
        self.btn_apply.Bind(wx.EVT_BUTTON, self.onApply)

    def onApply(self, event):  # noqa: D401
        # Persist to parent attributes (even if legacy widgets removed)
        self.parent._rfid_port_value = self.txt_port.GetValue().strip()
        self.parent._rfid_baud_value = int(self.spin_baud.GetValue())
        self.parent._rfid_autostart = self.chk_autostart.GetValue()
        if hasattr(self.parent, 'write_user_config'):
            try:
                self.parent.write_user_config()
            except Exception:
                pass
        if hasattr(self.parent, 'statusbar'):
            self.parent.statusbar.SetStatusText('RFID config applied')

    def onClose(self, event):  # noqa: D401
        self.onApply(None)
        self.EndModal(wx.ID_OK)


__all__ = [
    'SoftMouseConfigDialog',
    'RFIDConfigDialog',
]
