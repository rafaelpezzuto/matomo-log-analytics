# coding=utf-8
import logging
import magic
import os
import subprocess
import time

from libs.lib_database import (
    update_log_file_summary,
    update_log_file_status,
    update_date_status,
    get_lines_parsed,
    get_recent_log_files
)
from libs.lib_file_name import (
    add_summary_extension,
    FILE_GUNZIPPED_LOG_EXTENSION,
    add_gunzip_extension, FILE_LOG_EXTENSION
)
from libs.lib_status import LOG_FILE_STATUS_LOADING, LOG_FILE_STATUS_INVALID, LOG_FILE_STATUS_LOADED

DIR_WORKING_LOGS = os.environ.get('DIR_WORKING_LOGS', '/app/data/working')
DIR_SUMMARY = os.environ.get('DIR_SUMMARY', '/app/data/summary')
LOG_FILE_DATABASE_STRING = os.environ.get('LOG_FILE_DATABASE_STRING', 'mysql://user:pass@localhost:3306/matomo')

LOAD_FILES_LIMIT = int(os.environ.get('LOAD_FILES_LIMIT', 10))
COLLECTION = os.environ.get('COLLECTION', 'scl')
MATOMO_ID_SITE = os.environ.get('MATOMO_ID_SITE', '1')
MATOMO_API_TOKEN = os.environ.get('MATOMO_API_TOKEN', 'e536004d5816c66e10e23a80fbd57911')
MATOMO_URL = os.environ.get('MATOMO_URL', 'http://localhost')
MATOMO_RECORDERS = os.environ.get('MATOMO_RECORDERS', '12')
RETRY_DIFF_LINES = os.environ.get('RETRY_DIFF_LINES', '110000')

LOGGING_LEVEL = os.environ.get('LOGGING_LEVEL', 'INFO')


def get_available_log_files(database_uri, collection, dir_working_logs, load_files_limit):
    files_names = set([f for f in os.listdir(dir_working_logs) if os.path.isfile(os.path.join(dir_working_logs, f))])

    db_files = get_recent_log_files(database_uri,
                                    collection,
                                    ignore_status_list=[LOG_FILE_STATUS_LOADED, LOG_FILE_STATUS_INVALID])

    db_files_with_start_lines = [(db_f.id, db_f.name, get_lines_parsed(database_uri, db_f.id)) for db_f in db_files]

    available_lf = set()

    file_counter = 0
    for i in db_files_with_start_lines:
        id, name, start_line = i

        gz_name = add_gunzip_extension(name)
        full_path_gz_name = os.path.join(dir_working_logs, gz_name)

        alf = (id, full_path_gz_name, start_line)

        if gz_name in files_names:
            available_lf.add(alf)

            file_counter += 1
            if file_counter >= load_files_limit:
                break

    return available_lf


def generate_import_logs_params(in_file_path, out_file_path, start_line):
    matomo_attrs = {'--url': MATOMO_URL,
                    '--idsite': MATOMO_ID_SITE,
                    '--recorders': MATOMO_RECORDERS,
                    '--token-auth': MATOMO_API_TOKEN,
                    '--output': os.path.join(DIR_SUMMARY, out_file_path)}

    params = ' '.join(map('='.join, matomo_attrs.items()))

    params += ' --show-progress'

    if start_line > 0:
        params += ' --skip=' + str(start_line)

    return params + ' ' + in_file_path


def count_total_lines(log_file):
    output_line = subprocess.check_output(['wc', '-l', log_file])

    total_lines = output_line.split(' ')
    if total_lines and total_lines[0].isdigit():
        return int(total_lines[0])
    return -1


def main():
    logging.basicConfig(level=LOGGING_LEVEL,
                        format='[%(asctime)s] %(levelname)s %(message)s',
                        datefmt='%d/%b/%Y %H:%M:%S')

    if not os.path.exists(DIR_SUMMARY):
        os.makedirs(DIR_SUMMARY)

    files = get_available_log_files(LOG_FILE_DATABASE_STRING, COLLECTION, DIR_WORKING_LOGS, LOAD_FILES_LIMIT)

    for file_attrs in files:
        file_id, file_path, start_line = file_attrs
        time_start = time.time()

        logging.info('Uncompressing %s' % file_path)
        if not file_path.endswith(FILE_GUNZIPPED_LOG_EXTENSION) and not file_path.endswith(FILE_LOG_EXTENSION):
            logging.error('File %s does not have a valid extension (e.g. ".gz", ".log")' % file_path)
            exit(1)

        gunzipped_file_path = file_path.replace(FILE_GUNZIPPED_LOG_EXTENSION, '')

        file_type = magic.from_buffer(open(file_path, 'rb').read(2048), mime=True)
        if 'application/gzip' in file_type:
            subprocess.call('gunzip %s' % file_path, shell=True)
        else:
            if file_path.endswith(FILE_GUNZIPPED_LOG_EXTENSION):
                logging.warning('File %s is not compressed. Removing extension ".gz"' % file_path)
                os.rename(file_path, gunzipped_file_path)

        summary_path_output = add_summary_extension(gunzipped_file_path)

        total_lines = count_total_lines(gunzipped_file_path)

        logging.info('Loading %s' % gunzipped_file_path)
        update_log_file_status(LOG_FILE_DATABASE_STRING, COLLECTION, file_id, LOG_FILE_STATUS_LOADING)
        import_logs_params = generate_import_logs_params(gunzipped_file_path, summary_path_output, start_line)
        subprocess.call('python2 import_logs.py' + ' ' + import_logs_params, shell=True)

        logging.info('Updating log_file_summary with %s' % summary_path_output)
        full_path_summary_output = os.path.join(DIR_SUMMARY, summary_path_output)
        status = update_log_file_summary(LOG_FILE_DATABASE_STRING, full_path_summary_output, total_lines, file_id)

        logging.info('Removing file %s' % gunzipped_file_path)
        os.remove(gunzipped_file_path)

        logging.info('Updating log_file for row %s' % file_id)
        update_log_file_status(LOG_FILE_DATABASE_STRING, COLLECTION, file_id, status)

        logging.info('Updating date_status')
        update_date_status(LOG_FILE_DATABASE_STRING, COLLECTION)

        time_end = time.time()
        logging.info('Time spent: (%.2f) seconds' % (time_end - time_start))
