import asyncio
import base64
import copy
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .corrections import apply_reply
from .diagrams import render_svg, template
from .exporting import backup_project, export_docx, read_zip, restore_backup
from .images import MAX_UPLOAD, crop, decode_upload, digest, fingerprint, png, raster
from .models import PageDocument
from .provider import Provider, ProviderError
from .store import Conflict, Store, dumps, empty_document, now, uid

STATIC=Path(__file__).parent/'static'


def public_page(page):
    return {k:v for k,v in page.items() if k not in ('original','image','sha','phash')}


class ProjectInput(BaseModel):
    title: str = Field(min_length=1,max_length=160)
    subject: str = Field(default='Общее',min_length=1,max_length=160)


class EditInput(BaseModel):
    document: PageDocument
    version: int
    source_number: str | None = Field(default=None,max_length=80)


class ReplyInput(BaseModel):
    message: str = Field(min_length=1,max_length=4000)
    version: int
    question_id: str | None = None
    block_id: str | None = None
    remember: bool = True


def create_app(data_dir=None, provider_factory=Provider, seed_profile=False):
    store=Store(data_dir or os.getenv('NOTEBOOK_DATA',str(Path(__file__).parents[1]/'.data')))
    if seed_profile:
        store.seed_context_notes(json.loads((Path(__file__).parent/'context_notes.json').read_text(encoding='utf-8'))['notes'])
    provider=provider_factory(store)

    def page_view(page):
        result=public_page(page)
        if page['status']=='processing':
            with store.connect() as db:
                job=db.execute("SELECT status FROM jobs WHERE page_id=? AND status IN ('queued','running') ORDER BY created DESC LIMIT 1",(page['id'],)).fetchone()
            result['processing_phase']='Анализ изображения' if job and job['status']=='running' else 'В очереди'
        return result

    async def worker():
        while True:
            with store.connect() as db:
                job=db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                if job: db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(now(),job['id']))
            if not job:
                await asyncio.sleep(.4); continue
            pid=job['page_id']
            try:
                page=store.page(pid); project=store.project(page['project_id'])
                document=await provider.recognize(page,project)
                document=PageDocument.model_validate(document).model_dump()
                with store.connect() as db:
                    db.execute('INSERT INTO recognition_runs VALUES(?,?,?,?,?,?)',(uid(),pid,dumps(document),None,provider.settings(public=True)['model'],now()))
                current=store.page(pid)
                if current['version']!=job['base_version']:
                    raise ProviderError('Страница изменилась во время запроса. Результат сохранён отдельно в истории распознаваний; текущие правки сохранены.')
                store.update_page(pid,document,'Распознавание',current['version'],allow_processing=True)
                with store.connect() as db: db.execute("UPDATE jobs SET status='done',updated=? WHERE id=?",(now(),job['id']))
            except asyncio.CancelledError:
                # Kept as running: the next process explicitly marks the interrupted request.
                raise
            except Exception as e:
                text=str(e) if isinstance(e,(ProviderError,Conflict,ValueError,KeyError)) else 'Внутренняя ошибка обработки. Сохранённые правки не потеряны.'
                with store.connect() as db:
                    db.execute("UPDATE pages SET status='error',error=?,updated=? WHERE id=?",(text,now(),pid))
                    db.execute("UPDATE jobs SET status='error',error=?,updated=? WHERE id=?",(text,now(),job['id']))
                    db.execute('INSERT INTO recognition_runs VALUES(?,?,?,?,?,?)',(uid(),pid,None,text,provider.settings(public=True)['model'],now()))

    @asynccontextmanager
    async def lifespan(app):
        store.recover_jobs()
        task=asyncio.create_task(worker())
        yield
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    app=FastAPI(title='Тетрадь — рукописные конспекты',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store=store; app.state.provider=provider
    password=os.getenv('APP_PASSWORD','')
    authkey=hashlib.sha256((password+str(store.root)).encode()).digest()
    def auth_cookie(expiry):
        stamp=str(expiry)
        return stamp+'.'+hmac.new(authkey,stamp.encode(),hashlib.sha256).hexdigest()
    def authenticated(request):
        if not password: return True
        cookie=request.cookies.get('notebook_session','')
        try:
            stamp=cookie.split('.')[0]
            return int(stamp)>time.time() and hmac.compare_digest(cookie,auth_cookie(int(stamp)))
        except (ValueError,IndexError): return False

    @app.middleware('http')
    async def protect(request,call_next):
        if request.method in ('POST','PUT','PATCH','DELETE'):
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return JSONResponse({'detail':'Запрос с другого сайта запрещён'},status_code=403)
        if request.url.path not in ('/login','/api/health') and not authenticated(request):
            if request.url.path.startswith('/api/'): return JSONResponse({'detail':'Требуется вход'},status_code=401)
            return HTMLResponse(login_html)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
        return response

    login_html='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Вход — Тетрадь</title><body style="font:18px system-ui;max-width:400px;margin:15vh auto;padding:24px"><h1>Тетрадь</h1><p>Введите пароль приложения.</p><form method="post" action="/login"><input type="password" name="password" required autocomplete="current-password" aria-label="Пароль"><button>Войти</button></form></body></html>'''

    @app.post('/login')
    async def login(request:Request,password_value:str=Form('',alias='password')):
        if not password or secrets.compare_digest(password_value,password):
            response=HTMLResponse('<meta http-equiv="refresh" content="0;url=/">')
            response.set_cookie('notebook_session',auth_cookie(int(time.time())+7*86400),httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=7*86400)
            return response
        return HTMLResponse(login_html+'<p>Неверный пароль</p>',status_code=401)

    @app.exception_handler(KeyError)
    async def missing(request,exc): return JSONResponse({'detail':str(exc).strip("'")},status_code=404)
    @app.exception_handler(Conflict)
    async def conflict(request,exc): return JSONResponse({'detail':str(exc)},status_code=409)
    @app.exception_handler(ValueError)
    async def invalid(request,exc): return JSONResponse({'detail':str(exc)},status_code=422)
    @app.exception_handler(ProviderError)
    async def provider_error(request,exc): return JSONResponse({'detail':str(exc)},status_code=503)

    @app.get('/api/health')
    async def health(): return {'ok':True,'application':'Тетрадь','storage':'sqlite'}

    @app.get('/')
    async def index(): return FileResponse(STATIC/'index.html')

    @app.get('/api/projects')
    async def projects():
        with store.connect() as db:
            return [dict(r) for r in db.execute('SELECT p.*, (SELECT count(*) FROM pages WHERE project_id=p.id) AS page_count FROM projects p ORDER BY updated DESC')]

    @app.post('/api/projects',status_code=201)
    async def create_project(body:ProjectInput):
        ident=uid()
        with store.connect() as db: db.execute('INSERT INTO projects VALUES(?,?,?,?,?,NULL)',(ident,body.title.strip(),body.subject.strip(),now(),now()))
        return store.project(ident)

    @app.patch('/api/projects/{pid}')
    async def rename_project(pid:str,body:ProjectInput):
        store.project(pid)
        with store.connect() as db: db.execute('UPDATE projects SET title=?,subject=?,updated=? WHERE id=?',(body.title.strip(),body.subject.strip(),now(),pid))
        return store.project(pid)

    @app.delete('/api/projects/{pid}')
    async def delete_project(pid:str):
        store.project(pid)
        with store.connect() as db:
            if db.execute("SELECT 1 FROM jobs j JOIN pages p ON j.page_id=p.id WHERE p.project_id=? AND j.status IN ('queued','running')",(pid,)).fetchone():
                raise Conflict('Дождитесь завершения обработки проекта')
            paths=[p[k] for p in db.execute('SELECT original,image FROM pages WHERE project_id=?',(pid,)) for k in ('original','image')]
            base=db.execute('SELECT base_docx FROM projects WHERE id=?',(pid,)).fetchone()['base_docx']
            db.execute('DELETE FROM projects WHERE id=?',(pid,))
        store.prune(paths+[base])
        return {'ok':True}

    @app.get('/api/projects/{pid}/pages')
    async def pages(pid:str):
        store.project(pid)
        with store.connect() as db: ids=[r['id'] for r in db.execute('SELECT id FROM pages WHERE project_id=? ORDER BY position,id',(pid,))]
        return [page_view(store.page(i)) for i in ids]

    async def read_upload(file,limit=MAX_UPLOAD):
        data=await file.read(limit+1)
        if len(data)>limit: raise ValueError(f'Размер файла превышает {limit//1024//1024} МБ')
        return data

    @app.post('/api/projects/{pid}/upload')
    async def upload(pid:str,files:list[UploadFile]=File(...)):
        store.project(pid)
        if not 1<=len(files)<=40: raise ValueError('Выберите от 1 до 40 файлов')
        prepared=[]; total=0
        for file in files:
            data=await read_upload(file); total+=len(data)
            if total>200*1024*1024: raise ValueError('Одна загрузка ограничена 200 МБ')
            name=Path(file.filename or 'image.png').name
            images=await run_in_threadpool(decode_upload,data,name)
            if len(prepared)+len(images)>150: raise ValueError('За один раз можно добавить не более 150 страниц')
            original=store.save(data,Path(name).suffix.lower())
            for index,image in enumerate(images):
                encoded=png(image)
                prepared.append({'image':store.save(encoded,'.png'),'original':original,'sha':digest(encoded),'phash':fingerprint(image),'width':image.width,'height':image.height,'filename':name+(f' · лист {index+1}' if len(images)>1 else '')})
        results=[]; duplicates=[]
        with store.connect() as db:
            position=db.execute('SELECT coalesce(max(position),0) FROM pages WHERE project_id=?',(pid,)).fetchone()[0]
            existing=[dict(r) for r in db.execute('SELECT id,sha,phash,source_number FROM pages WHERE project_id=?',(pid,))]
            for item in prepared:
                ident=uid(); position+=1
                duplicate=next((p for p in existing if p['sha']==item['sha'] or (int(p['phash'],16)^int(item['phash'],16)).bit_count()<=4),None)
                page={'id':ident,'project_id':pid,'position':position,'source_number':str(position),**item,'status':'uploaded','document':dumps(empty_document()),'version':1,'error':None,'duplicate_of':duplicate['id'] if duplicate else None,'created':now(),'updated':now()}
                keys=list(page); db.execute(f'INSERT INTO pages ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',list(page.values()))
                store.snapshot(db,ident,'Загрузка оригинала')
                if duplicate: duplicates.append({'page_id':ident,'existing_id':duplicate['id'],'existing_number':duplicate['source_number'],'exact':duplicate['sha']==item['sha']})
                existing.append({'id':ident,'source_number':str(position),**item})
                results.append(ident)
            db.execute('UPDATE projects SET updated=? WHERE id=?',(now(),pid))
        return {'pages':[page_view(store.page(i)) for i in results],'duplicates':duplicates}

    @app.put('/api/projects/{pid}/order')
    async def order(pid:str,request:Request):
        store.project(pid); ids=(await request.json()).get('ids',[])
        with store.connect() as db:
            actual={r['id'] for r in db.execute('SELECT id FROM pages WHERE project_id=?',(pid,))}
            if len(ids)!=len(actual) or set(ids)!=actual: raise Conflict('Список страниц изменился. Обновите проект.')
            for pos,ident in enumerate(ids,1): db.execute('UPDATE pages SET position=? WHERE id=?',(pos,ident))
        return {'ok':True}

    @app.get('/api/pages/{page_id}')
    async def page(page_id:str): return page_view(store.page(page_id))

    @app.get('/api/pages/{page_id}/image')
    async def image(page_id:str): return FileResponse(store.path(store.page(page_id)['image']),media_type='image/png')

    @app.get('/api/pages/{page_id}/original')
    async def original(page_id:str):
        page=store.page(page_id)
        return FileResponse(store.path(page['original']),filename=page['filename'].split(' · лист ')[0])

    @app.get('/api/pages/{page_id}/crop/{block_id}')
    async def image_crop(page_id:str,block_id:str):
        page=store.page(page_id); block=next((b for b in page['document']['blocks'] if b['id']==block_id),None)
        if not block: raise KeyError('Блок не найден')
        return Response(crop(store,page,block.get('bbox')),media_type='image/png')

    @app.get('/api/pages/{page_id}/diagram/{block_id}')
    async def diagram(page_id:str,block_id:str):
        page=store.page(page_id); block=next((b for b in page['document']['blocks'] if b['id']==block_id),None)
        if not block or not block.get('diagram'): raise KeyError('Схема не найдена')
        return Response(render_svg(block['diagram']),media_type='image/svg+xml',headers={'Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'"})

    @app.post('/api/diagram-preview')
    async def diagram_preview(request:Request):
        return Response(render_svg(await request.json()),media_type='image/svg+xml',headers={'Content-Security-Policy':"default-src 'none'"})

    @app.get('/api/diagram-templates/{kind}')
    async def diagram_template(kind:str): return template(kind)

    @app.put('/api/pages/{page_id}')
    async def edit(page_id:str,body:EditInput):
        old=store.page(page_id); doc=body.document.model_dump()
        originals={b['id']:b for b in old['document']['blocks']}
        for b in doc['blocks']:
            if b['id'] in originals:
                b['original_text']=originals[b['id']]['original_text']; b['original_latex']=originals[b['id']]['original_latex']
        result=store.update_page(page_id,doc,'Ручная правка',body.version,source_number=body.source_number)
        return page_view(result)

    @app.delete('/api/pages/{page_id}')
    async def delete_page(page_id:str):
        page=store.page(page_id)
        if page['status']=='processing': raise Conflict('Дождитесь завершения распознавания')
        with store.connect() as db: db.execute('DELETE FROM pages WHERE id=?',(page_id,))
        store.prune([page['original'],page['image']])
        return {'ok':True}

    @app.post('/api/pages/{page_id}/recognize',status_code=202)
    async def recognize(page_id:str):
        provider.require(); page=store.page(page_id)
        with store.connect() as db:
            if db.execute("SELECT 1 FROM jobs WHERE page_id=? AND status IN ('queued','running')",(page_id,)).fetchone(): raise Conflict('Страница уже в очереди')
            ident=uid(); db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,NULL)',(ident,page_id,'queued',page['version'],now(),now()))
            db.execute("UPDATE pages SET status='processing',error=NULL WHERE id=?",(page_id,))
        return {'job_id':ident,'status':'queued'}

    def remember_page(page):
        project=store.project(page['project_id'])
        for block in page['document']['blocks']:
            if not block.get('bbox'): continue
            text=block['latex'] if block['type']=='formula' else block['text']
            if not text.strip() or '[?]' in text: continue
            with store.connect() as db:
                existing=db.execute('SELECT id,image FROM examples WHERE page_id=? AND block_id=?',(page['id'],block['id'])).fetchone()
            image=store.save(crop(store,page,block['bbox']),'.png')
            with store.connect() as db:
                if existing:
                    db.execute('UPDATE examples SET text=?,image=?,updated=? WHERE id=?',(text,image,now(),existing['id']))
                else:
                    db.execute('INSERT INTO examples VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),project['subject'],'subject',text,block.get('original_latex') if block['type']=='formula' else block.get('original_text',''),f'{project["title"]}, исходная стр. {page["source_number"]}',image,page['id'],block['id'],now(),now()))
            if existing: store.prune([existing['image']])

    @app.post('/api/pages/{page_id}/confirm')
    async def confirm(page_id:str,request:Request):
        body=await request.json(); page=store.page(page_id)
        if not page['document']['blocks']: raise ValueError('Добавьте содержимое страницы')
        if any(not q['resolved'] for q in page['document']['questions']): raise Conflict('Сначала ответьте на вопросы этой страницы или явно подтвердите «Всё верно».')
        page=store.update_page(page_id,page['document'],'Подтверждение страницы',body.get('version',-1),status='confirmed')
        if body.get('remember',True): remember_page(page)
        return page_view(page)

    @app.post('/api/pages/{page_id}/reply')
    async def reply(page_id:str,body:ReplyInput):
        page=store.page(page_id)
        if page['version']!=body.version: raise Conflict('Страница изменена. Обновите её.')
        doc,changes,confirmed,clarification=apply_reply(page['document'],body.message,body.block_id,body.question_id)
        if clarification: return {'needs_clarification':True,**clarification}
        if confirmed and not doc['blocks']: raise ValueError('Пустую страницу подтвердить нельзя')
        page=store.update_page(page_id,doc,'Ответ: '+body.message[:100],body.version,status='confirmed' if confirmed else 'review')
        with store.connect() as db:
            entries=changes or [(body.block_id,None,None)]
            for block_id,before,after in entries:
                db.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?,?)',(uid(),page_id,block_id,body.question_id,body.message,before,after,now()))
        if confirmed and body.remember: remember_page(page)
        return {'page':page_view(page),'needs_clarification':False}

    @app.get('/api/pages/{page_id}/history')
    async def history(page_id:str):
        store.page(page_id)
        with store.connect() as db:
            return {'versions':[dict(r) for r in db.execute('SELECT version,action,created FROM versions WHERE page_id=? ORDER BY version DESC',(page_id,))],
                    'corrections':[dict(r) for r in db.execute('SELECT * FROM corrections WHERE page_id=? ORDER BY created DESC',(page_id,))],
                    'recognitions':[dict(r) for r in db.execute('SELECT id,created,error,provider,document FROM recognition_runs WHERE page_id=? ORDER BY created DESC',(page_id,))]}

    @app.get('/api/pages/{page_id}/history/{number}')
    async def inspect_version(page_id:str,number:int):
        store.page(page_id)
        with store.connect() as db: row=db.execute('SELECT snapshot FROM versions WHERE page_id=? AND version=?',(page_id,number)).fetchone()
        if not row: raise KeyError('Версия не найдена')
        snap=json.loads(row['snapshot']); snap['document']=json.loads(snap['document'])
        return page_view(snap)

    @app.post('/api/pages/{page_id}/restore')
    async def restore_version(page_id:str,request:Request):
        body=await request.json(); page=store.page(page_id)
        with store.connect() as db: row=db.execute('SELECT snapshot FROM versions WHERE page_id=? AND version=?',(page_id,body.get('version'))).fetchone()
        if not row: raise KeyError('Версия не найдена')
        snap=json.loads(row['snapshot']); document=PageDocument.model_validate(json.loads(snap['document'])).model_dump()
        # Restoring is a new draft; it cannot silently retain a prior confirmation.
        return page_view(store.update_page(page_id,document,'Восстановление версии '+str(body['version']),body.get('current_version',-1),source_number=snap['source_number']))

    @app.get('/api/settings')
    async def settings(): return provider.settings(public=True)

    @app.put('/api/settings')
    async def update_settings(request:Request): return provider.save_settings(await request.json())

    @app.post('/api/projects/{pid}/import-docx')
    async def import_docx(pid:str,file:UploadFile=File(...)):
        store.project(pid); data=await read_upload(file,40*1024*1024)
        with read_zip(data,100*1024*1024) as z:
            if 'word/document.xml' not in z.namelist(): raise ValueError('Это не DOCX')
        from docx import Document
        try: Document(io.BytesIO(data))
        except Exception as e: raise ValueError('Не удалось прочитать DOCX') from e
        path=store.save(data,'.docx')
        with store.connect() as db: db.execute('UPDATE projects SET base_docx=?,updated=? WHERE id=?',(path,now(),pid))
        return {'ok':True,'filename':file.filename}

    @app.get('/api/projects/{pid}/export.docx')
    async def word(pid:str,drafts:bool=False,explanations:bool=False):
        data,warnings=await run_in_threadpool(export_docx,store,pid,drafts,explanations)
        return Response(data,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="notebook.docx"','X-Formula-Fallbacks':str(len(warnings))})

    @app.get('/api/projects/{pid}/backup.zip')
    async def backup(pid:str):
        data=await run_in_threadpool(backup_project,store,pid)
        return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="notebook-backup.zip"'})

    @app.post('/api/backups/restore')
    async def restore_project(file:UploadFile=File(...)):
        data=await read_upload(file,200*1024*1024)
        try: ident=await run_in_threadpool(restore_backup,store,data)
        except (KeyError,TypeError,zipfile.BadZipFile) as e: raise ValueError('Неполная или повреждённая резервная копия') from e
        return store.project(ident)

    @app.get('/api/profile')
    async def profile():
        with store.connect() as db:
            return {'examples':[dict(r) for r in db.execute('SELECT * FROM examples ORDER BY updated DESC')], 'notes':[dict(r) for r in db.execute('SELECT * FROM handwriting_notes ORDER BY created DESC')]}

    @app.post('/api/profile/examples',status_code=201)
    async def add_example(file:UploadFile=File(...),text:str=Form(...),scope:str=Form('global'),subject:str=Form(''),context:str=Form('')):
        if scope not in ('global','subject') or not text.strip() or len(text)>10000 or len(context)>4000 or len(subject)>160: raise ValueError('Проверьте текст образца и область применения')
        if scope=='subject' and not subject.strip(): raise ValueError('Укажите предмет')
        img=raster(await read_upload(file,20*1024*1024)); path=store.save(png(img),'.png'); ident=uid()
        with store.connect() as db: db.execute('INSERT INTO examples VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,subject,scope,text,'',context,path,None,None,now(),now()))
        return {'id':ident}

    @app.get('/api/profile/examples/{ident}/image')
    async def example_image(ident:str):
        with store.connect() as db: row=db.execute('SELECT image FROM examples WHERE id=?',(ident,)).fetchone()
        if not row: raise KeyError('Образец не найден')
        return FileResponse(store.path(row['image']),media_type='image/png')

    @app.patch('/api/profile/examples/{ident}')
    async def edit_example(ident:str,request:Request):
        value=await request.json()
        if not str(value.get('text','')).strip() or value.get('scope') not in ('global','subject'): raise ValueError('Нужны текст и область применения')
        with store.connect() as db:
            result=db.execute('UPDATE examples SET text=?,context=?,scope=?,subject=?,updated=? WHERE id=?',(str(value['text'])[:10000],str(value.get('context',''))[:4000],value['scope'],str(value.get('subject',''))[:160],now(),ident))
            if not result.rowcount: raise KeyError('Образец не найден')
        return {'ok':True}

    @app.delete('/api/profile/examples/{ident}')
    async def delete_example(ident:str):
        with store.connect() as db:
            row=db.execute('SELECT image FROM examples WHERE id=?',(ident,)).fetchone()
            db.execute('DELETE FROM examples WHERE id=?',(ident,))
        if row: store.prune([row['image']])
        return {'ok':True}

    @app.post('/api/profile/notes')
    async def add_note(request:Request):
        value=await request.json(); text=str(value.get('text','')).strip()
        if not text or len(text)>4000 or value.get('scope') not in ('global','subject'): raise ValueError('Укажите особенность почерка и область применения')
        ident=uid()
        with store.connect() as db: db.execute('INSERT INTO handwriting_notes VALUES(?,?,?,?,?)',(ident,value['scope'],str(value.get('subject',''))[:160],text,now()))
        return {'id':ident}

    @app.patch('/api/profile/notes/{ident}')
    async def edit_note(ident:str,request:Request):
        value=await request.json(); text=str(value.get('text','')).strip()
        if not text or len(text)>4000: raise ValueError('Введите текст особенности')
        with store.connect() as db:
            r=db.execute('UPDATE handwriting_notes SET text=? WHERE id=?',(text,ident))
            if not r.rowcount: raise KeyError('Заметка не найдена')
        return {'ok':True}

    @app.delete('/api/profile/notes/{ident}')
    async def delete_note(ident:str):
        with store.connect() as db: db.execute('DELETE FROM handwriting_notes WHERE id=?',(ident,))
        return {'ok':True}

    @app.post('/api/profile/import')
    async def import_profile(file:UploadFile=File(...)):
        data=await read_upload(file,100*1024*1024)
        with read_zip(data,200*1024*1024) as z:
            try: payload=json.loads(z.read('profile.json'))
            except (KeyError,ValueError) as e: raise ValueError('Нет корректного профиля почерка') from e
            if payload.get('format')!='handwriting-profile' or payload.get('version')!=1: raise ValueError('Неизвестный формат профиля')
            items=payload.get('examples',[]); notes=payload.get('notes',[])
            if len(items)>2000 or len(notes)>1000: raise ValueError('Слишком много образцов')
            prepared=[]
            for e in items:
                if e.get('scope') not in ('global','subject') or not str(e.get('text','')).strip(): raise ValueError('Неверная запись образца')
                name=e.get('image','')
                if not name.startswith('assets/'): raise ValueError('Неверный путь образца')
                image=raster(z.read(name)); prepared.append((e,png(image)))
            added=0
            with store.connect() as db:
                existing=[dict(x) for x in db.execute('SELECT text,subject,scope,image FROM examples')]
                seen={(x['text'],x['subject'],x['scope'],digest(store.path(x['image']).read_bytes())) for x in existing}
                for e,image in prepared:
                    key=(e['text'],e.get('subject',''),e['scope'],digest(image))
                    if key in seen: continue
                    path=store.save(image,'.png')
                    db.execute('INSERT INTO examples VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),str(e.get('subject',''))[:160],e['scope'],str(e['text'])[:10000],str(e.get('original_text',''))[:10000],str(e.get('context',''))[:4000],path,None,e.get('block_id'),now(),now()))
                    seen.add(key); added+=1
                for n in notes:
                    if n.get('scope') not in ('global','subject') or not str(n.get('text','')).strip(): raise ValueError('Неверная особенность почерка')
                    if not db.execute('SELECT 1 FROM handwriting_notes WHERE text=? AND subject=? AND scope=?',(n['text'],n.get('subject',''),n['scope'])).fetchone():
                        db.execute('INSERT INTO handwriting_notes VALUES(?,?,?,?,?)',(uid(),n['scope'],str(n.get('subject',''))[:160],str(n['text'])[:4000],now()))
        return {'added':added}

    @app.get('/api/profile/export.zip')
    async def export_profile():
        with store.connect() as db:
            data={'format':'handwriting-profile','version':1,'examples':[dict(r) for r in db.execute('SELECT * FROM examples')],'notes':[dict(r) for r in db.execute('SELECT * FROM handwriting_notes')]}
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('profile.json',dumps(data))
            for name in sorted({e['image'] for e in data['examples']}): z.write(store.path(name),name)
        return Response(out.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="handwriting-profile.zip"'})

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app


app=create_app(seed_profile=True)
