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
        colony_val = ''
        if hasattr(parent, 'softmouse_colony') and isinstance(parent.softmouse_colony, wx.TextCtrl):
            try:
                colony_val = parent.softmouse_colony.GetValue()
            except Exception:
                colony_val = ''
        self.txt_colony = wx.TextCtrl(pnl, value=colony_val)
        grid.Add(self.txt_colony, 1, wx.EXPAND)

        # Fast / Headful
        grid.Add(wx.StaticText(pnl, label='Fast Animals:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_fast = wx.CheckBox(pnl)
        if hasattr(parent, 'softmouse_fast') and isinstance(parent.softmouse_fast, wx.CheckBox):
            try:
                self.chk_fast.SetValue(parent.softmouse_fast.GetValue())
            except Exception:
                pass
        grid.Add(self.chk_fast)

        grid.Add(wx.StaticText(pnl, label='Headful Browser:'), 0, wx.ALIGN_CENTER_VERTICAL)
        self.chk_headful = wx.CheckBox(pnl)
        if hasattr(parent, 'softmouse_headful') and isinstance(parent.softmouse_headful, wx.CheckBox):
            try:
                self.chk_headful.SetValue(parent.softmouse_headful.GetValue())
            except Exception:
                pass
        grid.Add(self.chk_headful)

        # Force login, Save state, Parse
        self.chk_force_login = wx.CheckBox(pnl, label='Force fresh login')
        grid.Add(self.chk_force_login, 0, wx.ALIGN_LEFT)
        self.chk_save_state = wx.CheckBox(pnl, label='Save state after login')
        grid.Add(self.chk_save_state, 0, wx.ALIGN_LEFT)
        self.chk_parse = wx.CheckBox(pnl, label='Parse exported file')
        grid.Add(self.chk_parse, 0, wx.ALIGN_LEFT)

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
        if hasattr(self.parent, 'softmouse_colony') and isinstance(self.parent.softmouse_colony, wx.TextCtrl):
            self.parent.softmouse_colony.SetValue(self.txt_colony.GetValue().strip())
        if hasattr(self.parent, 'softmouse_fast') and isinstance(self.parent.softmouse_fast, wx.CheckBox):
            self.parent.softmouse_fast.SetValue(self.chk_fast.GetValue())
        if hasattr(self.parent, 'softmouse_headful') and isinstance(self.parent.softmouse_headful, wx.CheckBox):
            self.parent.softmouse_headful.SetValue(self.chk_headful.GetValue())
        # Store flags as attributes (future: persist to YAML)
        self.parent._softmouse_force_login = self.chk_force_login.GetValue()
        self.parent._softmouse_save_state = self.chk_save_state.GetValue()
        self.parent._softmouse_parse = self.chk_parse.GetValue()
        if hasattr(self.parent, 'statusbar'):
            self.parent.statusbar.SetStatusText('SoftMouse config updated')

    def onTestLogin(self, event):  # noqa: D401
        self._persist_to_parent()
        # Placeholder until login-only implemented in runner
        if hasattr(self.parent, 'statusbar'):
            colony = self.txt_colony.GetValue().strip()
            if not colony:
                self.parent.statusbar.SetStatusText('Enter colony name before test login')
                return
            self.parent.statusbar.SetStatusText('Test login triggered (full export until login-only added)')

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
        if hasattr(self.parent, 'rfid_port') and isinstance(self.parent.rfid_port, wx.TextCtrl):
            self.parent.rfid_port.SetValue(self.txt_port.GetValue().strip())
        if hasattr(self.parent, 'rfid_baud') and hasattr(self.parent.rfid_baud, 'SetValue'):
            try:
                self.parent.rfid_baud.SetValue(self.spin_baud.GetValue())
            except Exception:
                pass
        self.parent._rfid_autostart = self.chk_autostart.GetValue()
        if hasattr(self.parent, 'statusbar'):
            self.parent.statusbar.SetStatusText('RFID config applied')

    def onClose(self, event):  # noqa: D401
        self.onApply(None)
        self.EndModal(wx.ID_OK)


__all__ = [
    'SoftMouseConfigDialog',
    'RFIDConfigDialog',
]
