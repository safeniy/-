"""Cross-platform launcher; persistent data is independent of the working directory."""
import argparse
import os
from pathlib import Path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Тетрадь — рукописные конспекты')
    parser.add_argument('--host',default='127.0.0.1',help='Для телефона в локальной сети: 0.0.0.0')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--data-dir',default=os.getenv('NOTEBOOK_DATA',str(Path(__file__).parent.resolve()/'.data')))
    args=parser.parse_args()
    os.environ['NOTEBOOK_DATA']=str(Path(args.data_dir).resolve())
    import uvicorn
    uvicorn.run('notebook.app:app',host=args.host,port=args.port,workers=1)
