import sublime, sublime_plugin
from subprocess import call
import os, shutil, sys
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import trollop
import sublime_requests as requests

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
		self.key = conf.get('TRELLO_API_KEY')
		self.token = conf.get("TRELLO_TOKEN")
		self.board_id = conf.get("TRELLO_TEST_BOARD_ID")
		self.skip_lists = conf.get("SKIP_LISTS")

		trello_connection = trollop.TrelloConnection(self.key, self.token)

		try:
			self.safe_work(trello_connection, edit)
		except Exception as e:
			self.show_token_expired_help(e)
			raise e

	def show_token_expired_help(self, e):
		print("It seems your token is invalid or has expired, try adding it again.\nToken URL: %s" % self.token_url(), "The error encountered was: '%s'" % e)

	def token_url(self):
		return "https://trello.com/1/connect?key=%s&name=sublime_app&response_type=token&scope=read,write" % self.key


	def list_exists(self, list):
		trello_section = self.view.find('^## Trello warning', 0)
		match = self.view.find(list.name, 0, sublime.LITERAL)
		return match.begin() != -1 and match.begin() < trello_section.begin()

	def list_missing_lists(self, connection, edit):
		board = connection.get_board(self.board_id)
		lists = [list for list in board.lists if list.name not in self.skip_lists]

		missing_lists = [list for list in lists if not self.list_exists(list)]
		print('Missing lists', missing_lists)

		heading_region = self.view.find('^### Missing lists', 0)
		if heading_region.begin() == -1:
			print('Missing lists section not found')
			return

		line = self.view.line(heading_region)

		next_section_index = self.view.find('^##', line.end()).begin()

		replace_region = sublime.Region(line.end(), next_section_index)
		content = ''
		for list in missing_lists:
			content += '- ' + list.name + '\n'

		self.view.replace(edit, replace_region, '\n\n' + content + '\n')
		# content = 'Last updated: {}'.format(datetime.now().strftime("%Y-%m-%d"))

	def list_missing_cards(self, connection, edit):
		board = connection.get_board(self.board_id)
		lists = board.lists
		lists = [list for list in lists if list.name not in self.skip_lists]

		nested_cards = [list.cards for list in lists]
		cards = [card for sublist in nested_cards for card in sublist]
		print(len(cards), cards)
		# print("There are %d cards" % len(cards))
		# for card in cards:
		# 	print(card, card.list)

	def update_last_update(self, edit):
		heading_region = self.view.find('^## Trello warnings', 0)
		if heading_region.begin() == -1:
			print('Trello warnings section not found')
			return

		line = self.view.line(heading_region)

		next_section_index = self.view.find('^##', line.end()).begin()

		replace_region = sublime.Region(line.end(), next_section_index)
		content = 'Last synced: {}'.format(datetime.now().strftime("%Y-%m-%d %H:%m"))
		self.view.replace(edit, replace_region, '\n\n' + content + '\n\n')

	def safe_work(self, connection, edit):
		self.list_missing_lists(connection, edit)
		self.update_last_update(edit)
		# self.list_missing_cards(connection)
