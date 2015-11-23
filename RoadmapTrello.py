import sublime, sublime_plugin
from subprocess import call
import os, shutil, sys, re
from datetime import datetime, date
from collections import namedtuple, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import trollop
import sublime_requests as requests
from .roadmap_compiler_models import Task, Section, CategorySchedule, Statistics, DaySlot, human_duration


def plugin_loaded():
	if not os.path.exists(sublime.packages_path()+"/User/roadmap_trello.sublime-settings"):
		print(sublime.packages_path())
		shutil.copyfile(sublime.packages_path()+"/RoadmapCompile/roadmap_trello.sublime-settings", sublime.packages_path()+"/User/roadmap_trello.sublime-settings")

def extract_meta(task):
	# TODO: extract into utility function

	TASK_META_REGEX = '\[(?P<category1>\w{3})?\s?(?:(?P<duration_value1>\d{1,})(?P<duration_unit1>h|d|w|m|q))?\s?(?P<category2>\w{3})?\s?(?:(?P<duration_value2>\d{1,})(?P<duration_unit2>h|d|w|m|q))?\s?(?P<category3>\w{3})?\s?(?:(?P<duration_value3>\d{1,})(?P<duration_unit3>h|d|w|m|q))?\s?(?P<category4>\w{3})?\s?(?:(?P<duration_value4>\d{1,})(?P<duration_unit4>h|d|w|m|q))?\s?(?P<end_date>\d{4}-\d{2}-\d{2}.*)?\]$'
	DATE_FORMAT = '%Y-%m-%d'


	TaskMeta = namedtuple('TaskMeta', ['categories', 'end_date'])

	matches = re.search(TASK_META_REGEX, task)

	if matches:
		categories = {}
		if matches.group('category1'):
			categories[matches.group('category1')] = {}
			categories[matches.group('category1')]['duration_value'] = int(matches.group('duration_value1')) if matches.group('duration_value1') else None,
			categories[matches.group('category1')]['duration_unit'] = matches.group('duration_unit1')
		else:
			categories['None'] = {}
			categories['None']['duration_value'] = int(matches.group('duration_value1')) if matches.group('duration_value1') else None,
			categories['None']['duration_unit'] = matches.group('duration_unit1')
		if matches.group('category2'):
			categories[matches.group('category2')] = {}
			categories[matches.group('category2')]['duration_value'] = int(matches.group('duration_value2')) if matches.group('duration_value2') else None,
			categories[matches.group('category2')]['duration_unit'] = matches.group('duration_unit2')
		if matches.group('category3'):
			categories[matches.group('category3')] = {}
			categories[matches.group('category3')]['duration_value'] = int(matches.group('duration_value3')) if matches.group('duration_value3') else None,
			categories[matches.group('category3')]['duration_unit'] = matches.group('duration_unit3')
		if matches.group('category4'):
			categories[matches.group('category4')] = {}
			categories[matches.group('category4')]['duration_value'] = int(matches.group('duration_value4')) if matches.group('duration_value4') else None,
			categories[matches.group('category4')]['duration_unit'] = matches.group('duration_unit4')

		meta = TaskMeta(
			categories,
			datetime.strptime(matches.group('end_date'), DATE_FORMAT) if matches.group('end_date') else None
		)
		raw_meta = matches.group(0)
	else:
		raw_meta = ""
		categories = {}
		categories['None'] = {}
		categories['None']['duration_value'] = None,
		categories['None']['duration_unit'] = None
		meta = TaskMeta(categories, None)

	return meta

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
		self.skip_checklists = conf.get("SKIP_CHECKLISTS")

		trello_connection = trollop.TrelloConnection(self.key, self.token)

		try:
			self.safe_work(trello_connection, edit)
		except Exception as e:
			self.show_token_expired_help(e)
			raise e

	def show_token_expired_help(self, e):
		print("It seems your token is invalid or has expired, try adding it again.\nToken URL: %s" % self.token_url(), "The error encountered was: '%s'" % e)

	def token_url(self):
		return "https://trello.com/1/connect?key=%s&name=roadmap_trello&response_type=token&scope=read,write" % self.key


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

	def __compute_checkitem_duration(self, item):
		DEFAULT_CATEGORY_DURATION = 8

		meta = extract_meta(item._data['name'])

		resp = {}

		for category, dict in meta.categories.items():
			if not category in resp:
				resp[category] = DEFAULT_CATEGORY_DURATION

			if meta.categories[category]['duration_value'][0] is not None and meta.categories[category]['duration_unit'] is not None:
				resp[category] = meta.categories[category]['duration_value'][0] * Section.DURATION_MAP[meta.categories[category]['duration_unit']]

		return resp

	def __compute_card_duration(self, checkItems, duration_map):
		CardDuration = namedtuple('CardDuration', ['category', 'value'])
		DEFAULT_CARD_DURATION = 40

		if len(checkItems) == 0:
			return [CardDuration('None', DEFAULT_CARD_DURATION)]

		item_durations = {}
		for item in checkItems:
			durations = self.__compute_checkitem_duration(item)
			for key, value in durations.items():
			    if not key in item_durations:
			    	item_durations[key] = 0
			    item_durations[key] += value

		durations = []
		for key, value in item_durations.items():
		    temp = CardDuration(key,value)
		    durations.append(temp)

		return durations

	def __update_card_metadata(self, connection, edit, task, section_title):
		CARD_ID_REGEX = '.+https\:\/\/trello\.com\/c\/(?P<card_id>.+)\/.+'
		match = re.search(CARD_ID_REGEX, task)

		if not match:
			return

		card = connection.get_card(match.group('card_id'))
		checklists = [checklist for checklist in card.checklists if checklist._data['name'] not in self.skip_checklists]

		incomplete_items = []
		for checklist in checklists:
			its = [item for item in checklist.checkItems if item._data['state']=='incomplete']
			incomplete_items += its

		# Filter out cards with the "M"-aybe flag
		schedulable_items = []
		for item in incomplete_items:
			if not '[M ' in item._data['name'] and not '[M]' in item._data['name']:
				schedulable_items.append(item)

		# print('Kept {} sure items from a total of {}'.format(len(schedulable_items), len(incomplete_items)))

		card_name = card.name
		card_durations = self.__compute_card_duration(schedulable_items, Section.DURATION_MAP)
		card_duration_human = ''

		# Ensure None is the first category in the pipeline
		# Then I don't need to print it anymore, making my compiler smarter
		# as it will no longer distinguish between None and Non
		nonedu = [card for card in card_durations if card.category=="None"]
		if len(nonedu) > 0:
			card_durations.remove(nonedu[0])
			card_durations = nonedu + card_durations

		for dur in card_durations:
			category = '' if dur.category == 'None' else dur.category[:3] 
			card_duration_human += '{} {} '.format(category, human_duration(dur.value, Section.DURATION_MAP, max_segments=1))

		card_duration_human = card_duration_human.strip()
		deadline = extract_meta(task).end_date

		if deadline:
			new_meta = '[{} {}]'.format(card_duration_human, deadline.strftime("%Y-%m-%d"))
		else:
			new_meta = '[{}]'.format(card_duration_human)

		section_pos = self.view.find(section_title, 0, sublime.LITERAL)
		task_pos = self.view.find(task, section_pos.end(), sublime.LITERAL)

		# Update name
		end_name_pos = self.view.find(']', task_pos.begin(), sublime.LITERAL)
		region = sublime.Region(task_pos.begin() + 3, end_name_pos.begin())
		self.view.replace(edit, region, card_name)

		# Update meta
		needs_update = task.strip()[-1] == ']'
		line = self.view.line(task_pos.begin())

		if needs_update:
			update_pos = self.view.find('[', task_pos.begin() + 4, sublime.LITERAL)
			update_reg = sublime.Region(update_pos.begin(), line.end())
			self.view.replace(edit, update_reg, new_meta)
		else:
			self.view.insert(edit, line.end(), new_meta)


	def __update_card_section_metadata(self, connection, edit, tasks, section_title):
		# max_tasks = 10
		for task in tasks:
			self.__update_card_metadata(connection, edit, task, section_title)
			# max_tasks -= 1
			# if max_tasks == 0:
				# print('Stopped after %d tasks for development' % max_tasks)
				# break

	def update_cards_metadata(self, connection, edit, matches):

		for pair in matches:
			tasks = pair.section.lines
			self.__update_card_section_metadata(connection, edit, tasks, pair.section.title)

			# print('Only try the first section while developing')
			# break

	def safe_work(self, connection, edit):

		heading_region = self.view.find('^## Trello warnings', 0)
		if heading_region:
			if not 'ON' in self.view.substr(self.view.line(heading_region)):
				return

		content=self.view.substr(sublime.Region(0, self.view.size()))
		sections = self.__extract_sections(content)

		board = connection.get_board(self.board_id)
		lists = [list for list in board.lists if list.name not in self.skip_lists]
		matches = self.find_matching_sections(lists, sections)

		self.list_missing_lists(connection, edit)
		self.update_last_update(edit)
		self.add_missing_cards(connection, edit, matches)
		self.update_cards_metadata(connection, edit, matches)

