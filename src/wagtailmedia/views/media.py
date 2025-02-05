from __future__ import unicode_literals

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.vary import vary_on_headers

from wagtail.admin import messages
from wagtail.admin.auth import PermissionPolicyChecker, permission_denied
from wagtail.admin.forms.search import SearchForm
from wagtail.admin.models import popular_tags_for_model
from wagtail.core.models import Collection
from wagtail.search.backends import get_search_backends

from wagtailmedia.forms import get_media_form
from wagtailmedia.models import get_media_model
from wagtailmedia.permissions import permission_policy
from wagtailmedia.utils import paginate


permission_checker = PermissionPolicyChecker(permission_policy)


@permission_checker.require_any("add", "change", "delete")
@vary_on_headers("X-Requested-With")
def index(request):
    Media = get_media_model()

    # Get media files (filtered by user permission)
    media = permission_policy.instances_user_has_any_permission_for(
        request.user, ["change", "delete"]
    )

    # Ordering
    if "ordering" in request.GET and request.GET["ordering"] in [
        "title",
        "-created_at",
    ]:
        ordering = request.GET["ordering"]
    else:
        ordering = "-created_at"
    media = media.order_by(ordering)

    # Search
    query_string = None
    if "q" in request.GET:
        form = SearchForm(request.GET, placeholder=_("Search media files"))
        if form.is_valid():
            query_string = form.cleaned_data["q"]
            media = media.search(query_string)
    else:
        form = SearchForm(placeholder=_("Search media"))

    # Filter by collection
    current_collection = None
    collection_id = request.GET.get("collection_id")
    if collection_id:
        try:
            current_collection = Collection.objects.get(id=collection_id)
            media = media.filter(collection=current_collection)
        except (ValueError, Collection.DoesNotExist):
            pass

    # Filter by tag
    current_tag = request.GET.get("tag")
    if current_tag:
        try:
            media = media.filter(tags__name=current_tag)
        except (AttributeError):
            current_tag = None

    # Pagination
    paginator, media = paginate(request, media)

    collections = permission_policy.collections_user_has_any_permission_for(
        request.user, ["add", "change"]
    )
    if len(collections) < 2:
        collections = None

    # Create response
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(
            request,
            "wagtailmedia/media/results.html",
            {
                "ordering": ordering,
                "media_files": media,
                "query_string": query_string,
                "is_searching": bool(query_string),
            },
        )
    else:
        return render(
            request,
            "wagtailmedia/media/index.html",
            {
                "ordering": ordering,
                "media_files": media,
                "query_string": query_string,
                "is_searching": bool(query_string),
                "search_form": form,
                "popular_tags": popular_tags_for_model(Media),
                "current_tag": current_tag,
                "user_can_add": permission_policy.user_has_permission(
                    request.user, "add"
                ),
                "collections": collections,
                "current_collection": current_collection,
            },
        )


@permission_checker.require("add")
def add(request, media_type):
    Media = get_media_model()
    MediaForm = get_media_form(Media)

    if request.POST:
        media = Media(uploaded_by_user=request.user, type=media_type)
        form = MediaForm(request.POST, request.FILES, instance=media, user=request.user)
        if form.is_valid():
            form.save()

            # Reindex the media entry to make sure all tags are indexed
            for backend in get_search_backends():
                backend.add(media)

            messages.success(
                request,
                _("Media file '{0}' added.").format(media.title),
                buttons=[
                    messages.button(
                        reverse("wagtailmedia:edit", args=(media.id,)), _("Edit")
                    )
                ],
            )
            return redirect("wagtailmedia:index")
        else:
            messages.error(
                request, _("The media file could not be saved due to errors.")
            )
    else:
        media = Media(uploaded_by_user=request.user, type=media_type)
        form = MediaForm(user=request.user, instance=media)

    return render(
        request,
        "wagtailmedia/media/add.html",
        {
            "form": form,
            "media_type": media_type,
        },
    )


@permission_checker.require("change")
def edit(request, media_id):
    Media = get_media_model()
    MediaForm = get_media_form(Media)

    media = get_object_or_404(Media, id=media_id)

    if not permission_policy.user_has_permission_for_instance(
        request.user, "change", media
    ):
        return permission_denied(request)

    if request.POST:
        original_file = media.file
        form = MediaForm(request.POST, request.FILES, instance=media, user=request.user)
        if form.is_valid():
            if "file" in form.changed_data:
                # if providing a new media file, delete the old one.
                # NB Doing this via original_file.delete() clears the file field,
                # which definitely isn't what we want...
                original_file.storage.delete(original_file.name)
            media = form.save()

            # Reindex the media entry to make sure all tags are indexed
            for backend in get_search_backends():
                backend.add(media)

            messages.success(
                request,
                _("Media file '{0}' updated").format(media.title),
                buttons=[
                    messages.button(
                        reverse("wagtailmedia:edit", args=(media.id,)), _("Edit")
                    )
                ],
            )
            return redirect("wagtailmedia:index")
        else:
            messages.error(request, _("The media could not be saved due to errors."))
    else:
        form = MediaForm(instance=media, user=request.user)

    filesize = None

    # Get file size when there is a file associated with the Media object
    if media.file:
        try:
            filesize = media.file.size
        except OSError:
            # File doesn't exist
            pass

    if not filesize:
        messages.error(
            request,
            _(
                "The file could not be found. Please change the source or delete the media file"
            ),
            buttons=[
                messages.button(
                    reverse("wagtailmedia:delete", args=(media.id,)), _("Delete")
                )
            ],
        )

    return render(
        request,
        "wagtailmedia/media/edit.html",
        {
            "media": media,
            "filesize": filesize,
            "form": form,
            "user_can_delete": permission_policy.user_has_permission_for_instance(
                request.user, "delete", media
            ),
        },
    )


@permission_checker.require("delete")
def delete(request, media_id):
    Media = get_media_model()
    media = get_object_or_404(Media, id=media_id)

    if not permission_policy.user_has_permission_for_instance(
        request.user, "delete", media
    ):
        return permission_denied(request)

    if request.POST:
        media.delete()
        messages.success(request, _("Media file '{0}' deleted.").format(media.title))
        return redirect("wagtailmedia:index")

    return render(
        request,
        "wagtailmedia/media/confirm_delete.html",
        {
            "media": media,
        },
    )


def usage(request, media_id):
    Media = get_media_model()
    media = get_object_or_404(Media, id=media_id)

    paginator, used_by = paginate(request, media.get_usage())

    return render(
        request, "wagtailmedia/media/usage.html", {"media": media, "used_by": used_by}
    )
