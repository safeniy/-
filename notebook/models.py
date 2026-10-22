from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Block(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: Literal['text', 'heading', 'formula', 'figure']
    text: str = Field(default='', max_length=30000)
    original_text: str = Field(default='', max_length=30000)
    latex: str = Field(default='', max_length=6000)
    original_latex: str = Field(default='', max_length=6000)
    number: str = Field(default='', max_length=80)
    bbox: list[float] | None = None
    explanation: str = Field(default='', max_length=6000)
    diagram: dict | None = None
    figure_mode: Literal['original', 'redraw'] = 'original'
    redraw_confirmed: bool = False

    @model_validator(mode='after')
    def box(self):
        if self.bbox is not None:
            if len(self.bbox) != 4 or any(not 0 <= n <= 1 for n in self.bbox):
                raise ValueError('bbox: нужны нормированные x,y,ширина,высота')
            x, y, w, h = self.bbox
            if w <= 0 or h <= 0 or x+w > 1.001 or y+h > 1.001:
                raise ValueError('Область выходит за границы изображения')
        if self.type == 'figure' and self.bbox is None:
            raise ValueError('Для рисунка нужна область на фотографии')
        if self.figure_mode == 'redraw' and (not self.diagram or not self.redraw_confirmed):
            raise ValueError('Перерисовка должна быть просмотрена и подтверждена')
        if self.diagram:
            from .diagrams import render_svg
            render_svg(self.diagram)
        return self


class Question(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    block_id: str | None = None
    kind: Literal['uncertain', 'missing', 'possible_error'] = 'uncertain'
    prompt: str = Field(min_length=1, max_length=4000)
    options: list[str] = Field(default_factory=list, max_length=12)
    original: str = Field(default='', max_length=1000)
    bbox: list[float] | None = None
    resolved: bool = False
    answer: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def box(self):
        if self.bbox is not None:
            x = Block(id='check', type='text', bbox=self.bbox)
        return self


class PageDocument(BaseModel):
    blocks: list[Block] = Field(default_factory=list, max_length=250)
    questions: list[Question] = Field(default_factory=list, max_length=350)
    notes: str = Field(default='', max_length=10000)

    @model_validator(mode='after')
    def references(self):
        ids = [b.id for b in self.blocks]
        qids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids) or len(set(qids)) != len(qids):
            raise ValueError('Идентификаторы блоков и вопросов должны быть уникальны')
        if any(q.block_id and q.block_id not in ids for q in self.questions):
            raise ValueError('Вопрос ссылается на неизвестный блок')
        return self
