from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.core.exceptions import FieldError
from django.db.models import Model, QuerySet
from django.urls import NoReverseMatch, reverse


RELATED_RECORDS_LIMIT = 100


@dataclass(frozen=True)
class RelatedRecordAction:
    label: str
    url: str
    css_class: str = ''


@dataclass(frozen=True)
class RelatedRecordRow:
    label: str
    actions: tuple[RelatedRecordAction, ...]


@dataclass(frozen=True)
class RelatedRecordSection:
    title: str
    count: int
    records: tuple[RelatedRecordRow, ...]
    add_url: str | None
    list_url: str | None
    truncated: bool


class RelatedRecordsAdminMixin:
    """Expose reverse relations that are not already represented by an inline.

    Forward ForeignKey/OneToOne/ManyToMany fields continue to use Django's
    related-widget controls. Reverse ForeignKey and OneToOne relations are
    collected here so every registered related table remains reachable from
    either side of the relation.
    """

    related_records_limit = RELATED_RECORDS_LIMIT
    related_records_template = 'admin/journal/related_records.html'

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        context = dict(extra_context or {})
        if object_id:
            obj = self.get_object(request, unquote(object_id))
            if obj is not None:
                context['journal_related_sections'] = self.get_related_record_sections(
                    request,
                    obj,
                )
                context['journal_related_records_template'] = self.related_records_template
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=context,
        )

    def get_related_record_sections(self, request, obj: Model) -> tuple[RelatedRecordSection, ...]:
        inline_relations = self._inline_relation_keys(request, obj)
        sections: list[RelatedRecordSection] = []

        for relation in obj._meta.get_fields():
            if not relation.auto_created or relation.concrete:
                continue
            if not (relation.one_to_many or relation.one_to_one):
                continue
            if getattr(relation, 'hidden', False):
                continue

            related_model = relation.related_model
            relation_field = getattr(relation, 'field', None)
            relation_field_name = getattr(relation_field, 'name', None)
            if related_model is None or not relation_field_name:
                continue
            if related_model._meta.auto_created:
                continue
            if (related_model, relation_field_name) in inline_relations:
                continue

            related_admin = self.admin_site._registry.get(related_model)
            if related_admin is None:
                continue
            if not (
                related_admin.has_view_permission(request)
                or related_admin.has_change_permission(request)
            ):
                continue

            queryset = self._related_queryset(
                request,
                related_admin,
                relation_field_name,
                obj,
            )
            if queryset is None:
                continue

            # Fetch one extra row so the common case needs only one query per
            # relation. An exact COUNT is performed only for unusually large
            # relations that exceed the preview limit.
            preview_objects = list(queryset[: self.related_records_limit + 1])
            truncated = len(preview_objects) > self.related_records_limit
            if truncated:
                total = queryset.count()
                preview_objects = preview_objects[: self.related_records_limit]
            else:
                total = len(preview_objects)
            rows = tuple(
                self._related_record_row(request, related_admin, related_obj)
                for related_obj in preview_objects
            )
            add_url = self._related_add_url(
                request,
                related_admin,
                relation_field_name,
                obj,
            )
            list_url = self._admin_url(
                related_model,
                'changelist',
                query={
                    f'{relation_field_name}__id__exact': obj.pk,
                    **self._preserved_year_query(request),
                },
            )
            sections.append(
                RelatedRecordSection(
                    title=str(related_model._meta.verbose_name_plural),
                    count=total,
                    records=rows,
                    add_url=add_url,
                    list_url=list_url,
                    truncated=truncated,
                )
            )

        sections.sort(key=lambda section: section.title.casefold())
        return tuple(sections)

    def _inline_relation_keys(self, request, obj: Model) -> set[tuple[type[Model], str]]:
        keys: set[tuple[type[Model], str]] = set()
        for inline in self.get_inline_instances(request, obj):
            fk_name = inline.fk_name
            if not fk_name:
                try:
                    fk_name = next(
                        field.name
                        for field in inline.model._meta.fields
                        if field.remote_field
                        and field.remote_field.model is self.model
                    )
                except StopIteration:
                    continue
            keys.add((inline.model, fk_name))
        return keys

    @staticmethod
    def _related_queryset(
        request,
        related_admin: admin.ModelAdmin,
        relation_field_name: str,
        obj: Model,
    ) -> QuerySet | None:
        try:
            return (
                related_admin.get_queryset(request)
                .filter(**{relation_field_name: obj})
                .distinct()
            )
        except (FieldError, TypeError, ValueError):
            return None

    def _related_record_row(
        self,
        request,
        related_admin: admin.ModelAdmin,
        related_obj: Model,
    ) -> RelatedRecordRow:
        actions: list[RelatedRecordAction] = []
        model = related_obj._meta.model

        if related_admin.has_view_permission(request, related_obj):
            view_url = self._admin_url(
                model,
                'change',
                args=(related_obj.pk,),
                query=self._preserved_year_query(request),
            )
            if view_url:
                actions.append(RelatedRecordAction('Просмотреть', view_url, 'related-action-view'))

        if related_admin.has_change_permission(request, related_obj):
            change_url = self._admin_url(
                model,
                'change',
                args=(related_obj.pk,),
                query=self._preserved_year_query(request),
            )
            if change_url and not any(action.url == change_url for action in actions):
                actions.append(RelatedRecordAction('Редактировать', change_url, 'related-action-change'))
            elif change_url and actions:
                actions[0] = RelatedRecordAction('Просмотреть / редактировать', change_url, 'related-action-change')

        if related_admin.has_delete_permission(request, related_obj):
            delete_url = self._admin_url(
                model,
                'delete',
                args=(related_obj.pk,),
                query=self._preserved_year_query(request),
            )
            if delete_url:
                actions.append(RelatedRecordAction('Удалить', delete_url, 'related-action-delete'))

        label_builder = getattr(related_admin, 'get_related_record_label', None)
        label = label_builder(related_obj) if callable(label_builder) else str(related_obj)
        return RelatedRecordRow(label=str(label), actions=tuple(actions))

    def _related_add_url(
        self,
        request,
        related_admin: admin.ModelAdmin,
        relation_field_name: str,
        obj: Model,
    ) -> str | None:
        if not related_admin.has_add_permission(request):
            return None
        relation_field = related_admin.model._meta.get_field(relation_field_name)
        if not relation_field.editable:
            return None
        query = {
            relation_field_name: obj.pk,
            **self._preserved_year_query(request),
        }
        return self._admin_url(related_admin.model, 'add', query=query)

    @staticmethod
    def _preserved_year_query(request) -> dict[str, Any]:
        query: dict[str, Any] = {}
        for key in ('academic_year', 'year'):
            value = request.GET.get(key)
            if value:
                query[key] = value
        return query

    @staticmethod
    def _admin_url(
        model: type[Model],
        action: str,
        *,
        args: tuple[Any, ...] = (),
        query: dict[str, Any] | None = None,
    ) -> str | None:
        try:
            url = reverse(
                f'admin:{model._meta.app_label}_{model._meta.model_name}_{action}',
                args=args,
            )
        except NoReverseMatch:
            return None
        if query:
            return f'{url}?{urlencode(query)}'
        return url
