"""LaTeX -> editable Word math, with explicit handling of stretchy vector accents.

mathml2omml 0.0.2 emits an unbalanced groupChrPr for stretchy accents.
We handle those MathML nodes ourselves before invoking its other conversions;
we never ask the XML parser to recover or silently drop a malformed accent.
"""
from uuid import uuid4

import latex2mathml.converter
import mathml2omml
from lxml import etree

MATH_NS='http://schemas.openxmlformats.org/officeDocument/2006/math'
ML='http://www.w3.org/1998/Math/MathML'
NS={'m':MATH_NS}


def formula_element(latex):
    if len(latex)>6000: raise ValueError('Слишком длинная формула')
    parser=etree.XMLParser(resolve_entities=False,no_network=True)
    tree=etree.fromstring(latex2mathml.converter.convert(latex).encode(),parser)
    accents={}
    accent_chars=set('→←↔⃗^ˆ¯‾~˜⌢⌣⏞⏟_˙¨')
    for node in reversed(list(tree.iter())):
        tag=etree.QName(node).localname
        if tag not in ('mover','munder') or len(node)!=2: continue
        mark=node[1]
        symbol=''.join(mark.itertext())
        if etree.QName(mark).localname!='mo' or not (mark.get('stretchy')=='true' or symbol in accent_chars): continue
        token='ACC'+uuid4().hex
        accents[token]=(node[0],symbol,tag=='mover')
        placeholder=etree.Element('{'+ML+'}mi'); placeholder.text=token
        node.getparent().replace(node,placeholder)

    def convert(node):
        xml=etree.tostring(node,encoding='unicode')
        omml=mathml2omml.convert(xml)
        out=etree.fromstring(omml.replace('<m:oMath>',f'<m:oMath xmlns:m="{MATH_NS}">',1).encode(),parser)
        for text in list(out.xpath('.//m:r/m:t',namespaces=NS)):
            if text.text not in accents: continue
            base,symbol,above=accents[text.text]
            root=etree.Element('{'+ML+'}math',nsmap={None:ML});root.append(base)
            converted=convert(root)
            if above:
                accent=etree.Element('{'+MATH_NS+'}acc')
                pr=etree.SubElement(accent,'{'+MATH_NS+'}accPr')
                char=etree.SubElement(pr,'{'+MATH_NS+'}chr')
                char.set('{'+MATH_NS+'}val',{'→':'⃗','¯':'̅','‾':'̅','^':'̂','ˆ':'̂','~':'̃','˜':'̃'}.get(symbol,symbol))
            else:
                accent=etree.Element('{'+MATH_NS+'}groupChr')
                pr=etree.SubElement(accent,'{'+MATH_NS+'}groupChrPr')
                char=etree.SubElement(pr,'{'+MATH_NS+'}chr');char.set('{'+MATH_NS+'}val',symbol)
                pos=etree.SubElement(pr,'{'+MATH_NS+'}pos');pos.set('{'+MATH_NS+'}val','bot')
            arg=etree.SubElement(accent,'{'+MATH_NS+'}e')
            for child in list(converted): arg.append(child)
            run=text.getparent();run.getparent().replace(run,accent)
        return out
    return convert(tree)
