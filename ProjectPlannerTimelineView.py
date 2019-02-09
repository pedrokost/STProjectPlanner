import sublime
import sublime_plugin
import subprocess
import os
import sys
import tempfile
import codecs
import re
import json


def save_utf8(filename, text):
    """Save to UTF8 file."""
    with codecs.open(filename, 'w', encoding='utf-8')as f:
        f.write(text)


def load_utf8(filename):
    """Load UTF8 file."""
    with codecs.open(filename, 'r', encoding='utf-8') as f:
        return f.read()


def get_temp_preview_path(view):
    # TODO: filename could come from first line of projectplan.md file!
    tmp_filename = 'project-plan-%s.html' % view.id()
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


class ProjectPlannerTimelineView(sublime_plugin.TextCommand):

    def run(self, edit):
        # TODO: get the schedule

        # Open the file in the browser
        html = self.build_content()

        tmp_fullpath = get_temp_preview_path(self.view)
        save_utf8(tmp_fullpath, html)

        open_in_browser(tmp_fullpath)

    def build_content(self):
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

        data = []

        data = "var section_data = %s;" % (json.dumps(data), )

        # TODO: inject the data dictionary
        html = html.replace('// INJECT DATA HERE', data, 1)

        return html
