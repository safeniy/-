"""Real-browser manual workflow; generated fixtures, no claim of handwriting quality."""
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts';OUT.mkdir(exist_ok=True)


def image(seed):
    img=Image.new('RGB',(900,1250),'#fffefa');d=ImageDraw.Draw(img)
    for x in range(0,900,25):d.line((x,0,x,1250),fill='#dce5e4')
    for y in range(0,1250,25):d.line((0,y,900,y),fill='#dce5e4')
    font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    from PIL import ImageFont
    font=ImageFont.truetype(font,28) if Path(font).exists() else ImageFont.load_default()
    d.text((70,60),'ТЕСТОВАЯ СТРАНИЦА — НЕ ОБРАЗЕЦ ПОЧЕРКА',font=font,fill='#283f53')
    d.text((90,160),'тем повышается. В момент x.',font=font,fill='#283f53')
    d.text((90,240),'x(t) = x₀ + ∫ vₓ dt',font=font,fill='#283f53')
    d.line((120,800,700,800),fill='#283f53',width=3);d.line((120,800,120,430),fill='#283f53',width=3)
    d.line((120,800,650 if seed else 500,500),fill='#283f53',width=4)
    out=io.BytesIO();img.save(out,'PNG');return out.getvalue()


def run():
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='notebook-browser-') as root:
        env=os.environ.copy();env['NOTEBOOK_DATA']=root;env.pop('APP_PASSWORD',None);env.pop('OCR_API_KEY',None)
        log=open(OUT/'browser-server.log','w')
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','notebook.app:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        url=f'http://127.0.0.1:{port}'
        try:
            for _ in range(150):
                try:
                    if httpx.get(url+'/api/health').status_code==200:break
                except httpx.RequestError:pass
                time.sleep(.05)
            else:raise AssertionError('Server startup failed')
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                page=browser.new_page(viewport={'width':1600,'height':1050},device_scale_factor=1)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(url);page.click('#empty-start');page.fill('#p-title','Физика · проверка сценария');page.fill('#p-subject','Физика');page.click('#project-form button[type=submit],#project-form button.primary')
                expect(page.locator('#project-title')).to_have_text('Физика · проверка сценария')
                page.set_input_files('#upload-input',[{'name':'first.png','mimeType':'image/png','buffer':image(0)},{'name':'second.png','mimeType':'image/png','buffer':image(1)}])
                expect(page.locator('.page-item')).to_have_count(2)
                if page.locator('#keep-duplicates').is_visible():page.click('#keep-duplicates')
                def add_text(text,with_question=True):
                    page.click('#add-block');page.fill('#block-text',text)
                    if with_question:
                        page.locator('details').filter(has=page.locator('#question-prompt')).locator('summary').click()
                        page.fill('#question-prompt','Тем — это температура?');page.fill('#question-original','тем');page.fill('#question-options','температура; темп')
                        for field,v in zip(page.locator('.bbox-input').all(),['0.1','0.1','0.7','0.12']):field.fill(v)
                    page.click('#block-form button[type=submit]');expect(page.locator('#modal')).not_to_be_visible()
                add_text('Старый открытый вопрос: тем повышается.')
                first=page.locator('.page-item').nth(0).get_attribute('data-page')
                page.locator('.page-item').nth(1).click();add_text('тем повышается. В момент x.')
                second=page.locator('.page-item.active').get_attribute('data-page')
                page.click('#add-block');page.select_option('#block-type','formula');page.fill('#block-latex',r'x(t)=x_0+\int_{t_0}^{t}v_x\,dt');page.fill('#formula-number','3');
                for field,v in zip(page.locator('.bbox-input').all(),['0.1','0.18','0.7','0.1']):field.fill(v)
                page.click('#block-form button[type=submit]');expect(page.locator('#modal')).not_to_be_visible()
                page.click('#add-block');page.select_option('#block-type','figure');page.fill('#block-text','График скорости — исходный фрагмент')
                for field,v in zip(page.locator('.bbox-input').all(),['0.1','0.34','0.75','0.36']):field.fill(v)
                page.fill('#block-explanation','Качественная схема, без числовых значений.');page.click('#block-form button[type=submit]');expect(page.locator('#modal')).not_to_be_visible()
                page.locator('.question').click();expect(page.locator('.region.active')).to_have_count(1)
                page.fill('#reply-text','Тем — это температура');page.click('#reply-form button[type=submit]');expect(page.locator('.block-text').first).to_contain_text('температура')
                page.click('#all-correct');expect(page.locator('#page-status')).to_have_text('Подтверждена')
                old=page.request.get(url+f'/api/pages/{first}').json();assert old['status']=='review' and not old['document']['questions'][0]['resolved']
                assert page.locator('.katex').count()>0
                page.reload();expect(page.locator('.page-item')).to_have_count(2);page.locator(f'[data-page="{second}"]').click();expect(page.locator('#page-status')).to_have_text('Подтверждена')
                page.click('#zoom-in');expect(page.locator('#zoom-level')).to_have_text('125%');page.click('#zoom-fit')
                page.screenshot(path=OUT/'desktop-light.png',full_page=True)
                page.click('#theme-btn');page.screenshot(path=OUT/'desktop-dark.png',full_page=True)
                page.click('#export-btn')
                with page.expect_download() as info:page.click('#export-form button.primary')
                info.value.save_as(OUT/'verified-example.docx');page.click('#modal-close')
                # Phone layout in the same persisted project, no horizontal page overflow.
                page.set_viewport_size({'width':390,'height':844});page.click('[data-tab="text"]');expect(page.locator('.text-panel')).to_be_visible();expect(page.locator('.photo-panel')).not_to_be_visible()
                page.screenshot(path=OUT/'mobile-dark.png',full_page=True)
                page.click('[data-tab="questions"]');expect(page.locator('.questions-panel')).to_be_visible()
                page.click('[data-tab="photo"]');expect(page.locator('.photo-panel')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow'
                page.click('#settings-btn');expect(page.locator('#api-key')).to_be_visible();page.click('#modal-close')
                page.click('#profile-btn');expect(page.locator('.profile-example')).to_have_count(3);page.click('#modal-close')
                assert not errors,errors
                browser.close()
            print('PASS browser: multi-upload, block/formula/figure edits, image region, natural reply, page-scoped confirmation, reload, DOCX download, desktop/mobile, dark/light, profile; no JS errors')
        finally:
            proc.terminate();proc.wait(timeout=10);log.close()

if __name__=='__main__':run()
