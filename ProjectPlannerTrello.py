import sublime, sublime_plugin
from subprocess import call
import os, shutil, sys, re
from operator import attrgetter
from datetime import datetime, date
from collections import namedtuple, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import trollop
import sublime_requests as requests
from .models import Task, Section, CategorySchedule, Statistics, DaySlot, human_duration
from .utils import extract_task_metadata

class ProjectPlannerTrelloUp(sublime_plugin.TextCommand):

	def run(self, edit):
		conf = sublime.load_settings('project_planner.sublime-settings')
		self.key = conf.get('TRELLO_API_KEY')
		self.token = conf.get("TRELLO_TOKEN")
		self.board_id = conf.get("TRELLO_TEST_BOARD_ID")
		self.skip_lists = conf.get("SKIP_LISTS")
		self.done_lists = conf.get("DONE_LISTS")
		self.skip_checklists = conf.get("SKIP_CHECKLISTS")
		self.debug = False

		trello_connection = trollop.TrelloConnection(self.key, self.token)

		try:
			self.safe_work(trello_connection, edit)
		except Exception as e:
			self.show_token_expired_help(e)
			raise e

	def show_token_expired_help(self, e):
		print("It seems your token is invalid or has expired, try adding it again.\nToken URL: %s" % self.token_url(), "The error encountered was: '%s'" % e)

	def token_url(self):
		return "https://trello.com/1/connect?key=%s&name=project_planner&response_type=token&scope=read,write" % self.key

	def __upload_card_order_in_section(self, connection, section):
		trello_tasks = filter(lambda task: task.is_trello_card, section.tasks)
		last_pos = 100
		for task in trello_tasks:
			print('Set position {} for card {}'.format(last_pos, task.description))
			connection.set_card_position(task.trello_id, last_pos)
			last_pos += 100 # be nice with Trello by leaving gaps for reordering

	def __upload_card_order(self, connection, sections):
		for section in sections:
			self.__upload_card_order_in_section(connection, section)

	def safe_work(self, connection, edit):
		content=self.view.substr(sublime.Region(0, self.view.size()))
		sections = ProjectPlannerTrello(edit).extract_sections(content)

		self.__upload_card_order(connection, sections)


class ProjectPlannerTrello(sublime_plugin.TextCommand):
	"""
	https://github.com/sarumont/py-trello
	"""

	HEADING_IDENTIFIER = '#'
	SECTION_IDENTIFIER = '## '
	INVALID_SECTIONS = [
		'## Summary',
		'## Effort planning',
		'## Trello warnings'
	]

	def run(self, edit):
		print('Trello plugin run')
		conf = sublime.load_settings('project_planner.sublime-settings')
		self.key = conf.get('TRELLO_API_KEY')
		self.token = conf.get("TRELLO_TOKEN")
		self.board_id = conf.get("TRELLO_TEST_BOARD_ID")
		self.skip_lists = conf.get("SKIP_LISTS")
		self.done_lists = conf.get("DONE_LISTS")
		self.skip_checklists = conf.get("SKIP_CHECKLISTS")
		self.debug = False

		trello_connection = trollop.TrelloConnection(self.key, self.token)

		try:
			self.safe_work(trello_connection, edit)
		except Exception as e:
			self.show_token_expired_help(e)
			raise e

	def show_token_expired_help(self, e):
		print("It seems your token is invalid or has expired, try adding it again.\nToken URL: %s" % self.token_url(), "The error encountered was: '%s'" % e)

	def token_url(self):
		return "https://trello.com/1/connect?key=%s&name=project_planner&response_type=token&scope=read,write" % self.key


	def list_exists(self, list):
		trello_section = self.view.find('^## Trello warning', 0)
		match = self.view.find(list.name, 0, sublime.LITERAL)
		return match.begin() != -1 and match.begin() < trello_section.begin()

	def list_missing_lists(self, connection, edit):
		board = connection.get_board(self.board_id)
		lists = [list for list in board.lists if list.name not in self.skip_lists]

		missing_lists = [list for list in lists if not self.list_exists(list)]

		if len(missing_lists) > 0:
			self.errors.append({
				'category': 'Missing lists',
				'errors': [list.name for list in missing_lists]
			})

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
			return "\n- [" + card.name + '](' + self.url_core(card.url) + ')' 

		for card in cards:
			self.view.insert(edit, index, format_task(card))

	def url_core(self, url):
		REGEX = '(?P<url_core>https:\/\/trello.com\/c\/.+\/)(\d+-.+)?'
		return re.match(REGEX, url).group('url_core')

	def add_missing_cards(self, connection, edit, matches):
		def has_match(url, str_array):
			for str in str_array:
				if url in str:
					return True
			return False

		for pair in matches:
			missing_cards = [card for card in pair.list.cards if not has_match(self.url_core(card.url), pair.section.lines)]
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

	def extract_sections(self, content):
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

		meta = extract_task_metadata(item._data['name'])[0]

		resp = {}

		for category, dict in meta.categories.items():
			if not category in resp:
				resp[category] = DEFAULT_CATEGORY_DURATION

			if meta.categories[category]['duration_value'][0] is not None and meta.categories[category]['duration_unit'] is not None:
				resp[category] = meta.categories[category]['duration_value'][0] * Section.DURATION_MAP[meta.categories[category]['duration_unit']]

		return resp

	def __compute_card_duration(self, checkItems, duration_map, num_checklists, task):
		CardDuration = namedtuple('CardDuration', ['category', 'value'])
		DEFAULT_CARD_DURATION = 40
		COMPLETED_CARD_DURATION = 0

		if len(checkItems) == 0:
			if num_checklists > 0:
				self.add_error('Possibly Completed Cards', task)
				return [CardDuration('None', COMPLETED_CARD_DURATION)]
			else:
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

		if card.closed:
			self.add_error('Archived cards', card._data['name'])

		checklists = [checklist for checklist in card.checklists if checklist._data['name'] not in self.skip_checklists]

		incomplete_items = []
		for checklist in checklists:
			its = [item for item in checklist.checkItems if item._data['state']=='incomplete']
			incomplete_items += its

		# Filter out cards with the "M"-aybe flag
		optional_items = []
		schedulable_items = []
		for item in incomplete_items:
			if not '[M ' in item._data['name'] and not '[M]' in item._data['name']:
				schedulable_items.append(item)
			else:
				optional_items.append(item)

		# print('Kept {} sure items from a total of {}'.format(len(schedulable_items), len(incomplete_items)))

		card_name = card.name
		card_durations = self.__compute_card_duration(schedulable_items, Section.DURATION_MAP, len(checklists), task)
		card_duration_human = ''

		card_durations = sorted(card_durations, key=attrgetter('value'), reverse=True)

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
		deadline = extract_task_metadata(task)[0].end_date

		if deadline:
			new_meta = '[{} {}]'.format(card_duration_human, deadline.strftime("%Y-%m-%d"))
		elif card_duration_human:
			new_meta = '[{}]'.format(card_duration_human)
		elif len(optional_items) > 0:
			new_meta = '[M]'
		else:
			new_meta = ''

		section_pos = self.view.find(section_title, 0, sublime.LITERAL)
		task_pos = self.view.find(task, section_pos.end(), sublime.LITERAL)

		# Update name
		end_name_pos = self.view.find(']', task_pos.begin(), sublime.LITERAL)
		region = sublime.Region(task_pos.begin() + 3, end_name_pos.begin())
		self.view.replace(edit, region, card_name)

		# Use shorter trello url (to prevent name clashes)
		start_pos = self.view.find(']', task_pos.begin(), sublime.LITERAL)
		end_pos = self.view.find(')', start_pos.end(), sublime.LITERAL)
		region = sublime.Region(start_pos.end() + 1, end_pos.begin())
		self.view.replace(edit, region, self.url_core(card.url))

		# Update meta
		line = self.view.line(task_pos.begin())
		needs_update = self.view.substr(line).strip()[-1] == ']'

		if needs_update:
			update_pos = self.view.find('[', task_pos.begin() + 4, sublime.LITERAL)
			update_reg = sublime.Region(update_pos.begin(), line.end())
			self.view.replace(edit, update_reg, new_meta)
		else:
			self.view.insert(edit, line.end(), new_meta)

	def __update_card_section_metadata(self, connection, edit, tasks, section_title):
		max_tasks = 1
		for task in tasks:
			self.__update_card_metadata(connection, edit, task, section_title)
			max_tasks -= 1

			if self.debug:
				print(task)

			if self.debug and max_tasks == 0:
				print('Stopped after %d tasks for development' % max_tasks)
				break

	def add_error(self, category, error):
		exists = [err for err in self.errors if err['category'] == category]

		if exists:
			exists[0]['errors'].append(error)
		else:
			self.errors.append({
				'category': category,
				'errors': [error]
			})

	def update_cards_metadata(self, connection, edit, matches):

		for pair in matches:
			tasks = [task.raw for task in pair.section.tasks]
			self.__update_card_section_metadata(connection, edit, tasks, pair.section.title)

			if self.debug:
				break

	def display_errors(self, edit):
		heading_region = self.view.find('^### Errors', 0)
		if heading_region.begin() == -1:
			print('Errors section not found')
			return

		line = self.view.line(heading_region)

		next_section_index = self.next_section_start(line.end())

		replace_region = sublime.Region(line.end(), next_section_index)
		
		if len(self.errors) == 0:
			content = 'There are no errors\n\n'
		else:
			content = ''
			for errorgroup in self.errors:
				content += '**{}**:\n'.format(errorgroup['category'])
				for error in errorgroup['errors']:
					content += '- {}\n'.format(error)
				content += '\n'

		self.view.replace(edit, replace_region, '\n\n' + content + '')
		# content = 'Last updated: {}'.format(datetime.now().strftime("%Y-%m-%d"))

	def warn_incorrect_list_order(self, lists, sections):
		list_titles = [list._data['name'] for list in lists]
		section_titles = [section.title[3:] for section in sections if section.is_valid]

		indices = []
		for list_title in list_titles:
			try:
				indices.append(section_titles.index(list_title))
			except:
				indices.append(-1)

		for index_idx, index in enumerate(indices):
			if index_idx > 0:
				if indices[index_idx - 1] > index:
					self.add_error('List ordering', '*{}* should be placed before *{}*'.format(list_titles[index_idx-1], list_titles[index_idx]))

	def mark_completed(self, sections, edit, done_lists):
		"""
		Mark as completed each card in the DONE list
		"""

		def find_in_section(card, section_title):
			title_idx = self.view.find(section_title, 0)
			next_section = self.next_section_start(title_idx.end())
			card_idx = self.view.find(self.url_core(card.url), title_idx.end())
			if card_idx.end() > 0 and card_idx.end() < next_section:
				return self.view.line(card_idx.begin())
			else:
				return None

		completed_cards = [card for list in done_lists for card in list.cards]
		section_titles = [section.title for section in sections if section.is_valid]
		for section_title in section_titles:
			for completed_card in completed_cards:
				card_line = find_in_section(completed_card, section_title)
				if card_line:
				 	replace_region = sublime.Region(card_line.begin(), card_line.begin() + 1)
				 	self.view.replace(edit, replace_region, '+')


	def safe_work(self, connection, edit):

		self.errors = []
		self.debug = False

		if self.debug:
			print("DEBUG MODE IS ON")

		content=self.view.substr(sublime.Region(0, self.view.size()))
		sections = self.extract_sections(content)

		board = connection.get_board(self.board_id)
		lists = [list for list in board.lists if list.name not in self.skip_lists]
		done_lists = [list for list in board.lists if list.name in self.done_lists]
		matches = self.find_matching_sections(lists, sections)

		self.list_missing_lists(connection, edit)
		self.warn_incorrect_list_order(lists, sections)
		self.add_missing_cards(connection, edit, matches)
		self.update_cards_metadata(connection, edit, matches)
		self.mark_completed(sections, edit, done_lists)
		self.display_errors(edit)
		self.update_last_update(edit)
