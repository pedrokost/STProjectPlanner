import sublime
import sublime_plugin


class ProjectPlannerSave(sublime_plugin.EventListener):

    def on_pre_save(self, view):
        file_name = view.file_name()
        if file_name.endswith('.projectplan.md'):
            view.run_command('project_planner_compile')
            # import profile
            # profile.runctx("view.run_command('roadmap_compile')", {}, {'view': view}, filename="/home/pedro/roadmapcompileplugin.profile")
