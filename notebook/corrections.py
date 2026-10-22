import copy
import re


def apply_reply(document, message, block_id=None, question_id=None):
    doc=copy.deepcopy(document)
    blocks={b['id']:b for b in doc['blocks']}
    questions={q['id']:q for q in doc['questions']}
    if block_id and block_id not in blocks: raise ValueError('Выбранный блок не найден')
    if question_id and question_id not in questions: raise ValueError('Вопрос не найден')
    selected=questions.get(question_id)
    if selected and selected.get('block_id'): block_id=selected['block_id']
    clean=message.strip().strip('.!').lower().replace('ё','е')
    if clean in ('все верно','все правильно','остальное правильно','остальное верно'):
        for q in doc['questions']:
            if not q['resolved']: q.update(resolved=True,answer=message)
        return doc,[],True,None
    target_blocks=[blocks[block_id]] if block_id else list(blocks.values())
    axis=re.search(r'на\s+(вертикальной|горизонтальной)\s+оси\s+[«"\']?(.+?)[»"\']?\s+(?:меняем|заменить|замени)\s+на\s+[«"\']?(.+?)[»"\']?\.?$',message,re.I)
    pair=None
    if axis:
        pair=(axis[2].strip(),axis[3].strip().strip('«»".'))
        target_blocks=[b for b in target_blocks if b['type']=='figure']
    else:
        match=re.search(r'^\s*[«"\']?(.+?)[»"\']?\s*(?:[—–-]\s*это\s+|\s+это\s+)(.+?)\s*[.!]?$',message,re.I)
        if not match: match=re.search(r'^\s*(?:замени|заменить|меняем)\s+[«"\']?(.+?)[»"\']?\s+на\s+(.+?)\s*[.!]?$',message,re.I)
        if match: pair=(match[1].strip(' «»"'),match[2].strip(' «»".'))
    changes=[]
    if pair:
        old,new=pair
        pattern=re.compile(r'(?<!\w)'+re.escape(old)+r'(?!\w)',re.I)
        matches=[]
        for b in target_blocks:
            values=[b.get('text',''),b.get('latex','')]+[str(e.get('text','')) for e in (b.get('diagram') or {}).get('elements',[]) if e.get('type')=='text']
            count=sum(len(pattern.findall(v)) for v in values)
            if count: matches.append(b)
        if len(matches)!=1:
            return None,[],False,{'question':f'Где заменить «{old}» на «{new}»? Выберите конкретный блок или вопрос.', 'block_ids':[b['id'] for b in matches]}
        b=matches[0]
        before=b['text'] if b['type']!='formula' else b['latex']
        # One block is a deliberate scope; several occurrences inside it need explicit selection.
        for field in ('text','latex'):
            b[field]=pattern.sub(lambda _:new,b.get(field,''))
        if b.get('diagram'):
            for e in b['diagram']['elements']:
                if e.get('type')=='text': e['text']=pattern.sub(lambda _:new,e.get('text',''))
            b['redraw_confirmed']=False; b['figure_mode']='original'
        changes.append((b['id'],before,b['latex'] if b['type']=='formula' else b['text']))
        for q in doc['questions']:
            if q.get('block_id')==b['id'] and (q['id']==question_id or (q.get('original') and pattern.search(q['original']))):
                q.update(resolved=True,answer=message)
    elif selected:
        b=blocks.get(selected.get('block_id'))
        if clean in ('оставить как есть','так и написано','верно','правильно'):
            selected.update(resolved=True,answer=message)
        elif b:
            field='latex' if b['type']=='formula' else 'text'
            old=selected.get('original') or '[?]'
            if b[field].count(old)!=1:
                return None,[],False,{'question':'Укажите правку в редакторе блока: невозможно однозначно выбрать фрагмент для замены.', 'block_ids':[b['id']]}
            before=b[field]; b[field]=b[field].replace(old,message.strip(),1)
            selected.update(resolved=True,answer=message)
            changes.append((b['id'],before,b[field]))
        else:
            selected.update(resolved=True,answer=message)
    else:
        return None,[],False,{'question':'Выберите вопрос или блок, к которому относится исправление, либо откройте редактор блока.', 'block_ids':[]}
    return doc,changes,False,None
