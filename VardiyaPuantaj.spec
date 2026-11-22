# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['prototype_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('web', 'web'), ('templates', 'templates'), ('ocr_windows.ps1', '.')] + collect_data_files("tzdata"),
    hiddenimports=[
        'health', 'version', 'ui_settings', 'warning_rules', 'export_preview',
        'payroll_extras', 'workspace_tools', 'access', 'analytics', 'compliance',
        'controls', 'document_import', 'forms', 'planning', 'reports',
        'linked_leave', 'document_workflows', 'output_tracking', 'data_continuity',
        'access_services', 'operations', 'usability', 'mobile_service', 'live_assistant',
        'mail_automation', 'personnel_assets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VardiyaPuantaj',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
