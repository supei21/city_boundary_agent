from __future__ import annotations

from pydantic import BaseModel, Field


class ReportArticle(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)


class ReportSection(BaseModel):
    articles: list[ReportArticle] = Field(default_factory=list)


class ReportChapter(BaseModel):
    title: str = Field(min_length=1)
    sections: list[ReportSection] = Field(default_factory=list)


class PlanningPolicyDocument(BaseModel):
    title: str = Field(min_length=1)
    preface: str = Field(default="")
    chapters: list[ReportChapter] = Field(default_factory=list)

