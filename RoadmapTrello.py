import sublime, sublime_plugin
from subprocess import call
import os, shutil, sys
sys.path.insert(1,'./deps/usr/local/lib/python2.7/dist-packages')
from trello import TrelloClient


def plugin_loaded():
    if not os.path.exists(sublime.packages_path()+"/User/roadmap_trello.sublime-settings"):
        print(sublime.packages_path())
        shutil.copyfile(sublime.packages_path()+"/RoadmapCompile/roadmap_trello.sublime-settings", sublime.packages_path()+"/User/roadmap_trello.sublime-settings")



class RoadmapTrello(sublime_plugin.TextCommand):
	"""
	https://github.com/sarumont/py-trello
	"""
	def run(self, edit):
		print('Trello plugin run')
		conf = sublime.load_settings('roadmap_trello.sublime-settings')

		client = TrelloClient(
			api_key=conf.get('TRELLO_API_KEY'),
			api_secret=conf.get('TRELLO_API_SECRET'),
			token=conf.get('TRELLO_OAUTH_TOKEN'),
			token_secret=conf.get('TRELLO_OAUTH_TOKEN_SECRET')
		)

		# get list of all cards belonging to my board
		

		print(client)

		# load api key from preferences

