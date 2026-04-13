import sys
sys.path.insert(0, 'src')
try:
    import webui.app_impl as m
    print('WEBUI_OK')
except Exception as e:
    import traceback
    traceback.print_exc()
    print('IMPORT_FAILED:', e)
