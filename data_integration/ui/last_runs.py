"""Functions for visualizing the last runs of a pipeline node"""

import json

import flask
import psycopg2.extensions

import mara_db.postgresql
from mara_page import bootstrap, html, acl, _
from . import views
from .. import pipelines


def card(node: pipelines.Node) -> str:
    """A card that shows the system stats, the time line and output for the last runs or a node"""
    return bootstrap.card(
        id='last-runs-card',
        header_left=[
            'Last runs ',
            _.div(style='display:inline-block;margin-left:20px;')[html.asynchronous_content(
                flask.url_for('data_integration.last_runs_selector', path=node.url_path()))]],
        body=[html.spinner_js_function(),
              html.asynchronous_content(
                  url=flask.url_for('data_integration.system_stats', path=node.url_path(), run_id=None),
                  div_id='system-stats'),
              html.asynchronous_content(
                  url=flask.url_for('data_integration.timeline_chart', path=node.url_path(), run_id=None),
                  div_id='timeline-chart'),
              html.asynchronous_content(
                  url=flask.url_for('data_integration.run_output', path=node.url_path(), run_id=None, limit=True),
                  div_id='run-output')])


@views.blueprint.route('/<path:path>/last-runs-selector')
@views.blueprint.route('/last-runs-selector', defaults={'path': ''})
@acl.require_permission(views.acl_resource, do_abort=False)
def last_runs_selector(path: str):
    """
    Returns a html select element for selecting among the last runs of a node

    Args:
        path: The path of the node

    Returns:
        A `<select..><option ../><option ../></select>` element
    """
    from ..logging import node_cost

    node, __ = pipelines.find_node(path.split('/'))

    with mara_db.postgresql.postgres_cursor_context('mara') as cursor:  # type: psycopg2.extensions.cursor
        cursor.execute(f'''
SELECT
  run_id,
  to_char(start_time, 'Mon DD HH24:MI') AS start_time,
  extract(EPOCH FROM (end_time - start_time)) AS duration,
  succeeded
FROM data_integration_node_run
WHERE node_path = {"%s"}
ORDER BY run_id DESC;''', (node.path(),))

        return str(
            _.select(id='last-runs-selector', class_='custom-select', style="border:none",
                     onchange=f"nodePage.switchRun(this.value, '{path}')")[
                [_.option(value=str(run_id))[
                     f'{start_time}  ({node_cost.format_duration(duration)}, {"succeeded" if succeeded else "failed"})']
                 for run_id, start_time, duration, succeeded in cursor.fetchall()]])


@views.blueprint.route('/<path:path>/run-output', defaults={'run_id': None, 'limit': False})
@views.blueprint.route('/<path:path>/run-output/<int:run_id>', defaults={'limit': False})
@views.blueprint.route('/run-output', defaults={'path': '', 'run_id': None, 'limit': False})
@views.blueprint.route('/run-output/<int:run_id>', defaults={'path': '', 'limit': False})
@views.blueprint.route('/<path:path>/run-output-limited', defaults={'run_id': None, 'limit': True})
@views.blueprint.route('/<path:path>/run-output-limited/<int:run_id>', defaults={'limit': True})
@views.blueprint.route('/run-output-limited', defaults={'path': '', 'run_id': None, 'limit': True})
@views.blueprint.route('/run-output-limited/<int:run_id>', defaults={'path': '', 'limit': True})
@acl.require_permission(views.acl_resource, do_abort=False)
def run_output(path: str, run_id: int, limit: bool):
    """
    Returns the output of a node and its children as html

    Args:
        path: The path of the node
        run_id: The id of the run to return. If None, then the latest run is returned

    Returns:
        A <div class="run-output">..</div> element
    """
    node, __ = pipelines.find_node(path.split('/'))

    run_id = run_id or _latest_run_id(node.path())

    if not run_id:
        return ''

    line_limit = 1000
    with mara_db.postgresql.postgres_cursor_context('mara') as cursor:  # type: psycopg2.extensions.cursor
        cursor.execute(f'''
SELECT node_path, message, format, is_error
FROM data_integration_node_run
  JOIN data_integration_node_output USING (node_run_id)
WHERE node_path [1:{"%s"}] = %s 
      AND run_id = %s
ORDER BY timestamp 
''' + ('LIMIT ' + str(line_limit + 1) if limit else ''), (len(node.path()), node.path(), run_id))

        rows = cursor.fetchall()
        return str(_.script[f"""
nodePage.showOutput({json.dumps(rows[:line_limit] if limit else rows)},
               "{path}", 
               {'true' if len(rows) == line_limit + 1 else 'false'});
"""])


@views.blueprint.route('/<path:path>/system-stats', defaults={'run_id': None})
@views.blueprint.route('/<path:path>/system-stats/<int:run_id>')
@views.blueprint.route('/system-stats', defaults={'path': '', 'run_id': None})
@views.blueprint.route('/system-stats/<int:run_id>', defaults={'path': ''})
@acl.require_permission(views.acl_resource, do_abort=False)
def system_stats(path: str, run_id: int):
    node, __ = pipelines.find_node(path.split('/'))

    run_id = run_id or _latest_run_id(node.path())

    if not run_id:
        return ''

    with mara_db.postgresql.postgres_cursor_context('mara') as cursor:  # type: psycopg2.extensions.cursor
        cursor.execute(f'''
SELECT data_integration_system_statistics.*
FROM data_integration_system_statistics
  JOIN data_integration_node_run ON timestamp BETWEEN start_time AND end_time
WHERE run_id = {"%s"} AND node_path = {"%s"};''', (run_id, node.path()))

        data = [[row[0].isoformat()] + list(row[1:]) for row in cursor.fetchall()]
        if len(data) >= 15:
            return str(_.div(id='system-stats-chart', class_='google-chart')[' ']) \
                   + str(_.script[f'nodePage.showSystemStats({json.dumps(data)});'])
        else:
            return ''


@views.blueprint.route('/<path:path>/timeline-chart', defaults={'run_id': None})
@views.blueprint.route('/<path:path>/timeline-chart/<int:run_id>')
@views.blueprint.route('/timeline-chart', defaults={'path': '', 'run_id': None})
@views.blueprint.route('/timeline-chart/<int:run_id>', defaults={'path': ''})
@acl.require_permission(views.acl_resource, do_abort=False)
def timeline_chart(path: str, run_id: int):
    node, __ = pipelines.find_node(path.split('/'))

    run_id = run_id or _latest_run_id(node.path())

    if not run_id:
        return ''

    with mara_db.postgresql.postgres_cursor_context('mara') as cursor:  # type: psycopg2.extensions.cursor
        cursor.execute(f'''
SELECT node_path, start_time, end_time, succeeded, is_pipeline
FROM data_integration_node_run
WHERE node_path [1 :{'%(level)s'}] = {'%(node_path)s'}
      AND array_length(node_path, 1) > {'%(level)s'}  
      AND run_id = {'%(run_id)s'};''', {'level': len(node.path()), 'node_path': node.path(), 'run_id': run_id})

        nodes = [{'label': ' / '.join(node_path[len(node.path()):]),
                  'status': 'succeeded' if succeeded else 'failed',
                  'type': 'pipeline' if is_pipeline else 'task',
                  'url': flask.url_for('data_integration.node_page', path='/'.join(node_path)),
                  'start': start_time.isoformat(),
                  'end': end_time.isoformat()}
                 for node_path, start_time, end_time, succeeded, is_pipeline
                 in cursor.fetchall()]

        if nodes:
            return str(_.script[f"drawTimelineChart('timeline-chart', {json.dumps(nodes)})"])
        else:
            return ''


def _latest_run_id(node_path: [str]):
    with mara_db.postgresql.postgres_cursor_context('mara') as cursor:  # type: psycopg2.extensions.cursor
        cursor.execute('SELECT max(run_id) FROM data_integration_node_run WHERE node_path=%s', (node_path,))
        return cursor.fetchone()[0]
