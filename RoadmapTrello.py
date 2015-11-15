import sublime, sublime_plugin
from subprocess import call
import os, shutil, sys
from datetime import datetime, date
from collections import namedtuple, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import trollop
import sublime_requests as requests
from .roadmap_compiler_models import Task, Section, CategorySchedule, Statistics, DaySlot


def plugin_loaded():
	if not os.path.exists(sublime.packages_path()+"/User/roadmap_trello.sublime-settings"):
		print(sublime.packages_path())
		shutil.copyfile(sublime.packages_path()+"/RoadmapCompile/roadmap_trello.sublime-settings", sublime.packages_path()+"/User/roadmap_trello.sublime-settings")



class RoadmapTrello(sublime_plugin.TextCommand):
	"""
	https://github.com/sarumont/py-trello
	"""

	HEADING_IDENTIFIER = '#'
	SECTION_IDENTIFIER = '## '
	INVALID_SECTIONS = [
		'## Summary',
		'## Effort planning',
	]

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

		next_section_index = self.next_section_start(line.end())

		replace_region = sublime.Region(line.end(), next_section_index)
		content = ''
		for list in missing_lists:
			content += '- ' + list.name + '\n'

		self.view.replace(edit, replace_region, '\n\n' + content + '\n')
		# content = 'Last updated: {}'.format(datetime.now().strftime("%Y-%m-%d"))

	def find_matching_section(self, list, sections):

		list_name = "## " + list.name
		for section in sections:
			if section.is_valid and section.title == list_name:
				return section
				# print(list.name, section.title)
		return None

	def find_matching_sections(self, lists, sections):
		matches = []
		ListPair = namedtuple('ListPair', ['list', 'section'])
		for list in lists:
			section = self.find_matching_section(list, sections)
			if section is not None:
				matches.append(ListPair(list, section))
		return matches

	def insert_missing_cards(self, cards, section, edit):
		section_pos = self.view.find(section.title, 0, sublime.LITERAL)
		last_task_pos = self.view.find(section.tasks[-1].raw, section_pos.end(), sublime.LITERAL)

		index = last_task_pos.end() if last_task_pos.end() != -1 else section_pos.end()
		if index == -1:
			print('for some reason couldn\'t find location to insert the section')

		def format_task(card):
			return "\n- [" + card.name + '](' + card.url + ')' 

		for card in cards:
			self.view.insert(edit, index, format_task(card))

	def add_missing_cards(self, connection, edit, matches):
		def has_match(url, str_array):
			for str in str_array:
				if url in str:
					return True
			return False

		for pair in matches:
			missing_cards = [card for card in pair.list.cards if not has_match(card.url, pair.section.lines)]
			self.insert_missing_cards(missing_cards, pair.section, edit)

	def next_section_start(self, start=0, delimeter='^##'):
		next_section = self.view.find('^##', start).begin()
		if next_section == -1:
			next_section = self.view.size()

		return next_section

	def update_last_update(self, edit):
		heading_region = self.view.find('^## Trello warnings', 0)
		if heading_region.begin() == -1:
			print('Trello warnings section not found')
			return

		line = self.view.line(heading_region)

		next_section_index = self.next_section_start(line.end()) 
		replace_region = sublime.Region(line.end(), next_section_index)
		content = 'Last synced: {}'.format(datetime.now().strftime("%Y-%m-%d %H:%M"))
		self.view.replace(edit, replace_region, '\n\n' + content + '\n\n')


	def __section_indices(self, lines):
		SectionIndex = namedtuple('SectionIndex', ['index', 'is_valid'])
		indices = []
		for index, line in enumerate(lines):
			if line.startswith(self.HEADING_IDENTIFIER):
				is_valid_section = line.startswith(self.SECTION_IDENTIFIER) and \
					not line in self.INVALID_SECTIONS
				indices.append(SectionIndex(index, is_valid_section))
		indices.append(SectionIndex(len(lines), False))

		return indices

	def __extract_sections(self, content):
		# TODO: This is a copy-paste from RoadmapCompile. Extract into another
		# module

		array = content.split('\n')
		section_indices = self.__section_indices(array)

		sections = []

		for idx, sec_idx in enumerate(section_indices):
			if idx + 1 == len(section_indices): break
			start_idx = sec_idx.index
			end_idx = section_indices[idx+1].index

			is_section = sec_idx.is_valid
			section = Section(
				lines = array[start_idx:end_idx],
				is_valid = sec_idx.is_valid,
				row_at = start_idx
			)

			sections.append(section)

		return sections

	def safe_work(self, connection, edit):
		content=self.view.substr(sublime.Region(0, self.view.size()))
		sections = self.__extract_sections(content)

		board = connection.get_board(self.board_id)
		lists = [list for list in board.lists if list.name not in self.skip_lists]
		matches = self.find_matching_sections(lists, sections)

		self.list_missing_lists(connection, edit)
		self.update_last_update(edit)
		self.add_missing_cards(connection, edit, matches)

		# TODO: Update task title and duration
