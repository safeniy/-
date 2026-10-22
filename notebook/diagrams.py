import html
import math


def render_svg(diagram):
    # Structured primitives only: no executable SVG, URLs, foreignObject or CSS from a provider.
    if not isinstance(diagram,dict): raise ValueError('Нужна структура схемы')
    items = diagram.get('elements', [])
    if not isinstance(items, list) or len(items) > 250:
        raise ValueError('В схеме должно быть не более 250 элементов')
    def num(v):
        n = float(v)
        if not math.isfinite(n) or not -2000 <= n <= 4000:
            raise ValueError('Неверная координата схемы')
        return f'{n:g}'
    result = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700"><rect width="1000" height="700" fill="white"/><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#263445"/></marker></defs><g stroke="#263445" stroke-width="3" fill="none">']
    for item in items:
        if not isinstance(item,dict): raise ValueError('Неверный элемент схемы')
        kind = item.get('type')
        dash = ' stroke-dasharray="10 8"' if item.get('dashed') else ''
        arrow = ' marker-end="url(#arrow)"' if item.get('arrow') else ''
        if kind == 'line':
            a = ' '.join(f'{key}="{num(item[key])}"' for key in ('x1','y1','x2','y2'))
            result.append(f'<line {a}{dash}{arrow}/>')
        elif kind == 'polyline':
            pts = item.get('points', [])
            if not 2 <= len(pts) <= 300:
                raise ValueError('Неверный набор точек')
            values = ' '.join(f'{num(p[0])},{num(p[1])}' for p in pts)
            fill = '#d8e4ee' if item.get('shaded') else 'none'
            result.append(f'<polyline points="{values}" fill="{fill}"{dash}{arrow}/>')
        elif kind == 'circle':
            if not 0<float(item.get('r',5))<=1000: raise ValueError('Неверный радиус')
            result.append(f'<circle cx="{num(item["x"])}" cy="{num(item["y"])}" r="{num(item.get("r",5))}"/>')
        elif kind == 'text':
            if len(str(item.get('text','')))>200: raise ValueError('Подпись схемы слишком длинная')
            label = html.escape(str(item.get('text','')))
            result.append(f'<text x="{num(item["x"])}" y="{num(item["y"])}" font-size="26" font-family="DejaVu Sans,Arial,sans-serif" fill="#263445" stroke="none">{label}</text>')
        else:
            raise ValueError('Неизвестный тип элемента схемы')
    return ''.join(result) + '</g></svg>'


def template(kind):
    def line(a,b,c,d,**kw): return dict(type='line',x1=a,y1=b,x2=c,y2=d,**kw)
    def text(x,y,t): return dict(type='text',x=x,y=y,text=t)
    axes = [line(150,540,880,540,arrow=True),line(150,540,150,90,arrow=True)]
    if kind in ('position','velocity'):
        curve = [[150+x*6, 510-(x*x/32)] for x in range(0,101,2)]
        els = axes + [dict(type='polyline',points=curve),text(880,580,'t'),text(95,90,'x(t)' if kind=='position' else 'vₓ(t)')]
        if kind=='position':
            els += [line(230,500,780,190,dashed=True),line(540,480,870,130),text(740,155,'касательная'),text(300,510,'секущая')]
        else:
            els += [dict(type='polyline',points=[[300,540]]+[p for p in curve if 300<=p[0]<=600]+[[600,540],[300,540]],shaded=True),text(350,610,'Δx = ∫ vₓ dt')]
    elif kind=='parallelogram':
        els=[line(200,530,600,530,arrow=True),line(200,530,350,200,arrow=True),line(200,530,750,200,arrow=True),line(600,530,750,200,dashed=True),line(350,200,750,200,dashed=True),text(420,580,'a'),text(225,340,'b'),text(470,330,'a + b')]
    elif kind=='cross':
        els=[line(240,520,800,520,arrow=True),line(240,520,420,170,arrow=True),text(750,570,'a'),text(365,180,'b'),text(500,300,'⊙  a × b'),text(500,370,'⊗  −(a × b)')]
    elif kind=='components':
        els=[line(260,470,870,470,arrow=True),line(260,470,260,90,arrow=True),line(260,470,100,620,arrow=True),line(260,470,650,190,arrow=True),line(650,190,650,470,dashed=True),line(650,190,260,190,dashed=True),text(875,510,'x'),text(215,90,'z'),text(70,630,'y'),text(660,180,'a')]
    else:
        raise ValueError('Неизвестный шаблон')
    return {'elements':els}


def render_png(diagram):
    """Render the same validated primitives for Word without native Cairo dependencies."""
    import io
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont
    render_svg(diagram)  # One validation path for SVG and PNG.
    scale=1.5
    image=Image.new('RGB',(1500,1050),'white'); draw=ImageDraw.Draw(image)
    fonts=['DejaVuSans.ttf','C:/Windows/Fonts/seguisym.ttf','C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    font=None
    for name in fonts:
        try: font=ImageFont.truetype(name,39);break
        except OSError: continue
    if font is None: font=ImageFont.load_default(size=39)
    color='#263445'
    def pos(x,y):return float(x)*scale,float(y)*scale
    def segment(a,b,dashed=False,arrow=False):
        a=pos(*a);b=pos(*b);dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
        if dashed and length:
            t=0
            while t<length:
                end=min(t+15,length)
                draw.line((a[0]+dx*t/length,a[1]+dy*t/length,a[0]+dx*end/length,a[1]+dy*end/length),fill=color,width=4)
                t+=27
        else:draw.line((a,b),fill=color,width=4)
        if arrow and length:
            ux,uy=dx/length,dy/length
            draw.polygon([b,(b[0]-16*ux+6*uy,b[1]-16*uy-6*ux),(b[0]-16*ux-6*uy,b[1]-16*uy+6*ux)],fill=color)
    for e in diagram['elements']:
        if e['type']=='line':segment((e['x1'],e['y1']),(e['x2'],e['y2']),e.get('dashed'),e.get('arrow'))
        elif e['type']=='polyline':
            points=e['points']
            if e.get('shaded'):draw.polygon([pos(*p) for p in points],fill='#d8e4ee')
            for i in range(len(points)-1):segment(points[i],points[i+1],e.get('dashed'),e.get('arrow') and i==len(points)-2)
        elif e['type']=='circle':
            x,y=pos(e['x'],e['y']);r=float(e.get('r',5))*scale
            draw.ellipse((x-r,y-r,x+r,y+r),outline=color,width=4)
        elif e['type']=='text':draw.text(pos(e['x'],e['y']),str(e.get('text','')),font=font,fill=color,anchor='ls')
    out=io.BytesIO();image.save(out,'PNG');return out.getvalue()
