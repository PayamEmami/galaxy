"""
ISA datatype

See http://isa-tools.org

"""

from __future__ import print_function

import re
import os
import os.path
import sys
import glob
import json
import shutil
import zipfile
import logging
import tarfile
import tempfile
import csv
from isatools import isatab
from isatools import isajson
from json import dumps
from io import BytesIO
from cgi import escape
from galaxy import util
from galaxy.datatypes import data
from galaxy.datatypes import metadata
from galaxy.util.sanitize_html import sanitize_html
from galaxy import model

# CONSTANTS {{{1
################################################################

# Main files regex
JSON_FILE_REGEX = re.compile(r"^.*\.json$", flags = re.IGNORECASE)
INVESTIGATION_FILE_REGEX = re.compile(r"^i_\w+\.txt$", flags = re.IGNORECASE)

# The name of the ISA archive (compressed file) as saved inside Galaxy
ISA_ARCHIVE_NAME = "archive"

# Archives types
_FILE_TYPE_PREFIX = {
    "\x1f\x8b\x08": "gz",
    "\x42\x5a\x68": "bz2",
    "\x50\x4b\x03\x04": "zip"
}
_MAX_LEN_FILE_TYPE_PREFIX = max(len(x) for x in _FILE_TYPE_PREFIX)
_FILE_TYPE_REGEX = re.compile("(%s)" % "|".join(map(re.escape, _FILE_TYPE_PREFIX.keys())))

# Set max number of lines of the history peek
_MAX_LINES_HISTORY_PEEK = 11

# Configure logger {{{1
################################################################

logger = logging.getLogger(__name__)
ch = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(name)s %(levelname)s %(asctime)s %(message)s")
ch.setFormatter(formatter)
logger.handlers = []
logger.propagate = False
logger.addHandler(ch)
logger.setLevel(logging.ERROR)

# Function for opening correctly a CSV file for csv.reader() for both Python 2 and 3 {{{1
################################################################

def utf8_text_file_open(path):
    if sys.version_info[0] < 3: 
        fp = open(path, 'rb')
    else:
        fp = open(path, 'r', newline='', encoding='utf8')
    return fp

# ISA class {{{1
################################################################

class _Isa(data.Data):
    """ Base class for implementing ISA datatypes """
    composite_type = 'auto_primary_file'
    allow_datatype_change = False
    is_binary = True
    _main_file_regex = None

    # Make investigation instance {{{2
    ################################################################
    
    def _make_investigation_instance(self, filename):
        raise NotImplementedError()

    # Constructor {{{2
    ################################################################

    def __init__(self, main_file_regex, **kwd):
        super(_Isa, self).__init__(**kwd)
        self._main_file_regex = main_file_regex

        # Add the archive file as the only composite file
        self.add_composite_file(ISA_ARCHIVE_NAME, is_binary=True, optional=True)

    # Get ISA folder path {{{2
    ################################################################
    
    def _get_isa_folder_path(self, dataset):
        
        isa_folder = None
        
        if dataset:
            if isinstance(dataset, model.Dataset):
                isa_folder = dataset.extra_files_path
            if isinstance(dataset, model.HistoryDatasetAssociation):
                # XXX With this loop the dataset name is reset inside the history to the ISA archive ID. Why?
                for attr, value in dataset.__dict__.iteritems():
                    if str(attr) == '_metadata_collection':
                        datatype = value.parent.datatype
                isa_folder = dataset.dataset.extra_files_path

        if isa_folder is None:
            raise Exception('Unvalid dataset object, or no extra files path found for this dataset.')
        
        return isa_folder

    # Get main file {{{2
    ################################################################

    def _get_main_file(self, dataset):
        """Get the main file of the ISA archive. Either the investigation file i_*.txt for ISA-Tab, or the JSON file for ISA-JSON."""

        main_file = None
        isa_folder = self._get_isa_folder_path(dataset)

        if os.path.exists(isa_folder):
            
            # Get ISA archive older
            isa_files = os.listdir(isa_folder)

            # Try to find a JSON file
            main_file = self._find_main_file_in_archive(isa_files)

            if main_file is None:
                raise Exception('Invalid ISA archive. No main file found.')

            # Make full path
            main_file = os.path.join(isa_folder, main_file)

        return main_file

    # Get investigation {{{2
    ################################################################

    def _get_investigation(self, dataset):
        """Create a contained instance specific to the exact ISA type (Tab or Json).
           We will use it to parse and access information from the archive."""

        investigation = None
        main_file = self._get_main_file(dataset)
        if main_file is not None:
            investigation = self._make_investigation_instance(main_file)

        return investigation

    # Find main file in archive {{{2
    ################################################################
    
    def _find_main_file_in_archive(self, files_list):
        """Find the main file inside the ISA archive."""

        found_file = None
        
        for f in files_list:
            match = self._main_file_regex.match(f)
            if match:
                if found_file is None:
                    found_file = match.group()
                else:
                    raise Exception('More than one file match the pattern "', str(file_regex), '" to identify the investigation file')

        return found_file

    # Extract archive {{{2
    ################################################################

    def _extract_archive(self, stream, output_path=None):
        """Extract files from archive and put them is predefined folder."""

        # extract the archive to a temp folder
        if output_path is None:
            output_path = tempfile.mkdtemp()
        # try to detect the type of the compressed archive
        a_type = self._detect_file_type(stream)
        # decompress the archive
        if a_type == "zip":
            self._extract_zip_archive(stream, output_path)
        elif a_type == "gz":
            self._extract_tar_archive(stream, output_path)
        else:
            raise Exception("Not supported archive format!!!")

        return output_path

    # Extract ZIP archive {{{2
    ################################################################

    def _extract_zip_archive(self, stream, target_path):
        """Extract files from a ZIP archive."""

        temp_folder = tempfile.mkdtemp()
        data = BytesIO(stream.read())
        zip_ref = zipfile.ZipFile(data)
        zip_ref.extractall(path=temp_folder)
        self._move_to_target_path(temp_folder, target_path)

    # Extract TAR archive {{{2
    ################################################################

    def _extract_tar_archive(self, stream, target_path):
        """Extract files from a TAR archive."""
        
        # extract the TAR archive
        temp_folder = tempfile.mkdtemp()
        with tarfile.open(fileobj=stream) as tar:
            tar.extractall(path=temp_folder)
        self._move_to_target_path(temp_folder, target_path)

    # Move to target path {{{2
    ################################################################

    def _move_to_target_path(self, temp_folder, target_path, delete_temp_folder=True):
        """Move extracted files to the destination folder imposed by Galaxy."""

        # find the root folder containing the dataset
        tmp_subfolders = [f for f in os.listdir(temp_folder) if
                          not f.startswith(".") and f not in (ISA_ARCHIVE_NAME, "__MACOSX")]
        # move files contained within the root dataset folder to their target path
        root_folder = os.path.join(temp_folder, tmp_subfolders[0])
        if len(tmp_subfolders) == 1 and os.path.isdir(root_folder):
            # move the root dataset folder to its final destination and clean the temp data
            for f in os.listdir(root_folder):
                shutil.move(os.path.join(root_folder, f), target_path)
        elif len(tmp_subfolders) > 1:
            for f in tmp_subfolders:
                shutil.move(os.path.join(temp_folder, f), target_path)
        # clean temp data if required
        if delete_temp_folder:
            shutil.rmtree(temp_folder)

    # List archive files {{{2
    ################################################################

    def _list_archive_files(self, stream):
        """List files contained inside the ISA archive."""

        # try to detect the type of the compressed archive
        a_type = self._detect_file_type(stream)
        # decompress the archive
        if a_type == "zip":
            data = BytesIO(stream.read())
            zip_ref = zipfile.ZipFile(data)
            files_list = zip_ref.namelist()
        elif a_type == "gz":
            with tarfile.open(fileobj=stream) as tar:
                files_list = [i.name for i in tar]
        else:
            raise Exception("Not supported archive format!!!")
        # filter the base path if it exists
        if len(files_list) > 0:
            base_path = files_list[0].split("/")[0]
            if base_path:
                # the TAR archive encodes the base_path without a final '/'
                if base_path in files_list:
                    files_list.remove(base_path)
                # the ZIP archive encodes the base_path with a final '/'
                base_path = os.path.join(base_path, '')
                if base_path in files_list:
                    files_list.remove(base_path)
                # remove the base_path from all remaining files
                files_list = [f.replace(base_path, '') for f in files_list]
        return files_list

    # Detect file type {{{2
    ################################################################

    def _detect_file_type(self, stream):
        """
        Try to detect the type of the ISA archive: is it ZIP, or GUNZIP?

        :return: "zip" or "gz" if the file type is detected; None otherwise.
        """
        file_type = None
        file_start = stream.read(_MAX_LEN_FILE_TYPE_PREFIX)
        stream.seek(0)  # reset the stream
        matched_prefix = _FILE_TYPE_REGEX.match(file_start)
        if matched_prefix:
            file_type = _FILE_TYPE_PREFIX[matched_prefix.string[matched_prefix.start():matched_prefix.end()]]

        return file_type

    # Set peek {{{2
    ################################################################

    def set_peek(self, dataset, is_multi_byte=False):
        """Set the peek and blurb text. Get first lines of the main file and set it as the peek."""

        main_file = self._get_main_file(dataset)

        if main_file is None:
            raise RuntimeError("Unable to find the main file within the 'files_path' folder")

        # Read first lines of main file
        with open(main_file, "r") as f:
            data = []
            for line in f:
                if len(data) < _MAX_LINES_HISTORY_PEEK:
                    data.append(line)
                else:
                    break
            if not dataset.dataset.purged and data:
                dataset.peek = json.dumps({"data": data})
                dataset.blurb = 'data'
            else:
                dataset.peek = 'file does not exist'
                dataset.blurb = 'file purged from disk'

    # Display peek {{{2
    ################################################################

    def display_peek(self, dataset):
        """Create the HTML table used for displaying peek, from the peek text found by set_peek() method."""

        out = ['<table cellspacing="0" cellpadding="3">']
        try:
            if not dataset.peek:
                dataset.set_peek()
            json_data = json.loads(dataset.peek)
            for line in json_data["data"]:
                line = line.strip()
                if not line:
                    continue
                out.append('<tr><td>%s</td></tr>' % escape(util.unicodify(line, 'utf-8')))
            out.append('</table>')
            out = "".join(out)
        except Exception as exc:
            out = "Can't create peek %s" % str(exc)
        return out

    # Generate primary file {{{2
    ################################################################

    def generate_primary_file(self, dataset=None):
        """Generate the primary file. It is an HTML file containing description of the composite dataset
           as well as a list of the composite files that it contains."""

        if dataset:
            rval = ['<html><head><title>ISA Dataset </title></head><p/>']
            if hasattr(dataset, "extra_files_path"):
                rval.append('<div>ISA Dataset composed of the following files:<p/><ul>')
                for cmp_file in os.listdir(dataset.extra_files_path):
                    opt_text = ''
                    rval.append('<li><a href="%s" type="text/plain">%s</a>%s</li>' % (cmp_file, cmp_file, opt_text))
                rval.append('</ul></div></html>')
            else:
                rval.append('<div>ISA Dataset is empty!<p/><ul>')
            return "\n".join(rval)
        return "<div>No dataset available</div>"

    # Dataset content needs grooming {{{2
    ################################################################

    def dataset_content_needs_grooming(self, file_name):
        """This function is called on an output dataset file after the content is initially generated."""
        return True

    # Groom dataset content {{{2
    ################################################################

    def groom_dataset_content(self, file_name):
        """This method is called by Galaxy to extract files contained in a composite data type."""
        # XXX Is the right place to extract files? Should this step not be a cleaning step instead?
        # Could extracting be done earlier and composite files declared as files contained inside the archive
        # instead of the archive itself?

        # extract basename and folder of the current file whose content has to be groomed
        basename = os.path.basename(file_name)
        output_path = os.path.dirname(file_name)
        # extract archive if the file corresponds to the ISA archive
        if basename == ISA_ARCHIVE_NAME:
            # perform extraction
            with open(file_name, 'rb') as stream:
                self._extract_archive(stream, output_path=output_path)
            # remove the original archive file
            os.remove(file_name)
        
    # Set meta {{{2
    ################################################################

    def set_meta( self, dataset, overwrite=True, **kwd ):
        """Set meta data information."""
        super(Isa, self).set_meta(dataset, **kwd)
        self._set_dataset_name(dataset)
        return True

    # Set dataset name {{{2
    ################################################################
    
    def _set_dataset_name(self, dataset):
        investigation = self._get_investigation(dataset)
        if investigation is not None:
            dataset.name = investigation.identifier
        else:
            dataset.name = 'ISA DATASET'
        
    # Display data {{{2
    ################################################################


    def display_data(self, trans, dataset, preview=False, filename=None, to_ext=None, offset=None, ck_size=None, **kwd):
        """Downloads the ISA dataset if `preview` is `False`;
           if `preview` is `True`, it returns a preview of the ISA dataset as a HTML page.
           The preview is triggered when user clicks on the eye icon of the composite dataset."""

        self._set_dataset_name(dataset)
        # if it is not required a preview use the default behaviour of `display_data`
        if not preview:
            return super(_Isa, self).display_data(trans, dataset, preview, filename, to_ext, **kwd)

        # prepare the preview of the ISA dataset
        investigation = self._get_investigation(dataset)
        if investigation is None:
            html = """<html><header><title>Error while reading ISA archive.</title></header>
                   <body>
                        <h1>An error occured while reading content of ISA archive.</h1>
                        <p>If you have tried to load your archive with the uploader by selecting isa-tab as composite data type, then try to load it again with isa-json instead. Conversely, if you have tried to load your archive with the uploader by selecting isa-json as composite data type, then try isa-tab instead.</p>
                        <p>You may also try to look into your zip file in order to find out if this is a proper ISA archive. If you see a file i_Investigation.txt inside, then it is an ISA-Tab archive. If you see a file with extension .json inside, then it is an ISA-JSON archive. If you see nothing like that, then either your ISA archive is corrupted, or it is not an ISA archive.</p>
                   </body></html>"""
        else:
            html = '<html><body>'
            html += '<h1>{0} {1}</h1>'.format(investigation.title, investigation.identifier)
            
            # Loop on all studies
            for study in investigation.studies:
                html += '<h2>Study %s</h2>' % study.identifier
                html += '<h3>%s</h3>' % study.title
                html += '<p>%s</p>' % study.description
                html += '<p>Submitted the %s</p>' % study.submission_date
                html += '<p>Released on %s</p>' % study.public_release_date
                
                # Loop on all assays of this study
                for assay in study.assays:
                    html += '<h3>Assay %s</h3>' % assay.filename
                    html += '<p>Measurement type: %s</p>' % assay.measurement_type.term # OntologyAnnotation
                    html += '<p>Technology type: %s</p>' % assay.technology_type.term # OntologyAnnotation
                    html += '<p>Technology platform: %s</p>' % assay.technology_platform
                    if assay.data_files is not None:
                        html += '<p>Data files:</p>'
                        html += '<ul>'
                        for data_file in assay.data_files:
                            html += '<li>' + str(data_file.id) + ' - ' + str(data_file.filename) + ' - ' + str(data_file.label) + '</li>'
                        html += '</ul>'

            html += '</body></html>'

        # Set mime type
        mime = 'text/html'
        self._clean_and_set_mime_type(trans, mime)

        return sanitize_html(html).encode('utf-8')

# ISA-Tab class {{{1
################################################################

class IsaTab(_Isa):
    file_ext = "isa-tab"

    # Constructor {{{2
    ################################################################

    def __init__(self, **kwd):
        super(IsaTab, self).__init__(main_file_regex = INVESTIGATION_FILE_REGEX, **kwd)

    # Make investigation instance {{{2
    ################################################################
        
    def _make_investigation_instance(self, filename):

        # Parse ISA-Tab investigation file
        parser = isatab.InvestigationParser()
        fp = utf8_text_file_open(filename)
        parser.parse(fp)
        isa = parser.isa

        return isa

# ISA-JSON class {{{1
################################################################

class IsaJson(_Isa):
    file_ext = "isa-json"

    # Constructor {{{2
    ################################################################

    def __init__(self, **kwd):
        super(IsaJson, self).__init__(main_file_regex = JSON_FILE_REGEX, **kwd)

    # Make investigation instance {{{2
    ################################################################
        
    def _make_investigation_instance(self, filename):
        
        # Parse JSON file
        fp = utf8_text_file_open(filename)
        isa = isajson.load(fp)
            
        return isa
