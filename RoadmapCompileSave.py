import sublime, sublime_plugin

class RoadmapCompileSave(sublime_plugin.EventListener):
	def on_pre_save(self, view):
		file_name = view.file_name()
		if file_name.endswith('.roadmap.md'):
			print('RoadmapCompileSave: compiling roadmap')
			view.run_command('roadmap_trello')
			view.run_command('roadmap_compile')
			# import profile
			# profile.runctx("view.run_command('roadmap_compile')", {}, {'view': view}, filename="/home/pedro/roadmapcompileplugin.profile")

