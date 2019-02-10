import sublime
import sublime_plugin
import subprocess
import os
import sys
import tempfile
import codecs
import re
import datetime
import json
from .ProjectPlanner import ProjectPlannerCompile


def slugify(s):
    """
    Simplifies ugly strings into something URL-friendly.
    >>> print slugify("[Some] _ Article's Title--")
    some-articles-title
    """

    # "[Some] _ Article's Title--"
    # "[some] _ article's title--"
    s = s.lower()

    # "[some] _ article's_title--"
    # "[some]___article's_title__"
    for c in [' ', '-', '.', '/']:
        s = s.replace(c, '_')

    # "[some]___article's_title__"
    # "some___articles_title__"
    s = re.sub('\W', '', s)

    # "some___articles_title__"
    # "some   articles title  "
    s = s.replace('_', ' ')

    # "some   articles title  "
    # "some articles title "
    s = re.sub('\s+', ' ', s)

    # "some articles title "
    # "some articles title"
    s = s.strip()

    # "some articles title"
    # "some-articles-title"
    s = s.replace(' ', '-')

    return s


def save_utf8(filename, text):
    """Save to UTF8 file."""
    with codecs.open(filename, 'w', encoding='utf-8')as f:
        f.write(text)


def get_temp_preview_path(view, title):
    tmp_filename = 'project-plan-%s-%s.html' % (slugify(title), view.id())
    tmp_dir = tempfile.gettempdir()
    if not os.path.isdir(tmp_dir):  # create directory if not exists
        os.makedirs(tmp_dir)
    tmp_fullpath = os.path.join(tmp_dir, tmp_filename)
    return tmp_fullpath


def open_in_browser(filepath):
    """Open in browser for the appropriate platform."""

    if sys.platform.startswith('darwin'):
        subprocess.call(('open', filepath))
    elif os.name == 'nt':  # For Windows
        os.startfile(filepath)
    elif os.name == 'posix':  # For Linux, Mac, etc.
        subprocess.call(('xdg-open', filepath))

    sublime.status_message('ProjectPlanner Timeline launched in default browser')


def json_formatter(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()


class ProjectPlannerTimelineView(sublime_plugin.TextCommand):

    def run(self, edit):
        planner = ProjectPlannerCompile(self.view)
        planner.run(edit)
        sections = planner.sections

        # Open the file in the browser
        html, title = self.build_content(sections)

        tmp_fullpath = get_temp_preview_path(self.view, title)
        save_utf8(tmp_fullpath, html)

        open_in_browser(tmp_fullpath)

    def build_sections_data(self, sections):
        valid_sections = [s for s in sections if s.is_valid]

        groups = []
        items = []
        all_categories = []

        item_sequential_id = 1
        subgroup_sequential_id = 1

        min_date = datetime.datetime(5000, 1, 1, 0)
        max_date = datetime.datetime(1000, 1, 1, 0)

        for section_index, section in enumerate(valid_sections):
            if len(section.tasks) < 1:
                continue

            group_id = 'group-' + str(section_index)

            general_subgroup_stacking = {}
            for category in section.categories():
                general_subgroup_stacking['sg_' + category] = True
                all_categories.append(category)

            groups.append({
                'id': group_id,
                'content': section.pretty_title,
                'subgroupStack': general_subgroup_stacking,
                'value': section_index
            })

            tasks = section.tasks
            for task in tasks:
                categories = task.categories()

                subgroup_id = None
                # TODO: all items with a single category should belong to the same
                # subgroup, to fix stacking issues - for each category!

                # If task split into pieces, subgroup it
                if len(categories) > 1:
                    start = min([task.scheduled_start_date(c) for c in categories])
                    end = max([task.scheduled_end_date(c) for c in categories])

                    subgroup_id = 'sg_' + str(subgroup_sequential_id)
                    subgroup_sequential_id += 1

                    item = {
                        'id': item_sequential_id,
                        'content': task.name,
                        'start': start,
                        'end': end,
                        'group': group_id,
                        'subgroup': subgroup_id,
                        'className': 'task-group'
                    }
                    items.append(item)
                    item_sequential_id += 1

                for category in categories:
                    name = '<span class="item-category">' + category + '</span>'
                    if len(categories) <= 1:
                        name += ' ' + task.name

                    start = task.scheduled_start_date(category)
                    end = task.scheduled_end_date(category)

                    item = {
                        'id': item_sequential_id,
                        'content': name,
                        'start': start,
                        'end': end,
                        'group': group_id,
                        'subgroup': 'sg_' + category if subgroup_id is None else subgroup_id,
                        'className': '',
                        'category': category
                    }

                    if task.has_deadline:
                        item['className'] += ' deadline'

                    if item['end'] < datetime.datetime.today():
                        item['className'] += ' deadline-missed'

                    items.append(item)

                    item_sequential_id += 1

                    if start < min_date:
                        min_date = start

                    if end > max_date:
                        max_date = end

        return {
            'items': items,
            'groups': groups,
            'minDate': min_date,
            'maxDate': max_date,
            'categories': list(set(all_categories))
        }

    def build_content(self, sections):
        html = sublime.load_resource('Packages/ProjectPlanner/assets/index.html')

        str_list = html.split('\n')
        for index, row in enumerate(str_list):
            if 'data-replace-script' in row:
                m = re.search('src="([^\"]+)"', row)
                str_list[
                    index] = "<script>%s</script>" % sublime.load_resource('Packages/ProjectPlanner/assets/%s' % m.group(1))
            elif 'data-replace-stylesheet' in row:
                m = re.search('href="([^\"]+)"', row)
                str_list[
                    index] = "<style>%s</style>" % sublime.load_resource('Packages/ProjectPlanner/assets/%s' % m.group(1))

        html = "\n".join(str_list)

        data = self.build_sections_data(sections)

        data = "var sectionData = %s;" % (json.dumps(data, default=json_formatter), )

        html = html.replace('// INJECT DATA HERE', data, 1)
        title = sections[0].pretty_title
        html = html.replace('{{TITLE}}', title)

        return (html, title)
