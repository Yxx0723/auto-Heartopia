"""Independent deployment switches for collection optimization rollback."""
import os


def enabled(name):
    value=os.environ.get('HEARTOPIA_'+name,'1')
    if value not in ('0','1'):
        raise ValueError('HEARTOPIA_'+name+' must be 0 or 1')
    return value=='1'
