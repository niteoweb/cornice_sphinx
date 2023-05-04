# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
# Contributors: Vincent Fretin
"""
Sphinx extension that is able to convert a service into a documentation.
"""
from cornice.service import clear_services
from cornice.service import get_services
from cornice.util import is_string
from cornice.util import to_list
from docutils import core
from docutils import nodes
from docutils import statemachine
from docutils.parsers.rst import Directive
from docutils.parsers.rst import directives
from docutils.writers.html4css1 import HTMLTranslator
from docutils.writers.html4css1 import Writer
from importlib import import_module
from os.path import basename
from pyramid.path import DottedNameResolver
from sphinx.util.docfields import DocFieldTransformer, Field, TypedField

import docutils
import json
import sys
import typing as t

try:
    from importlib import reload
except ImportError:
    pass

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO


MODULES = {}
PY3 = sys.version_info[0] == 3


def convert_to_list(argument):
    """Convert a comma separated list into a list of python values"""
    if argument is None:
        return []
    else:
        return [i.strip() for i in argument.split(',')]


def convert_to_list_required(argument):
    if argument is None:
        raise ValueError('argument required but none supplied')
    return convert_to_list(argument)


def from_json_to_dict(argument):
    """Loads json data"""
    if argument is None:
        return {}
    else:
        return json.loads(argument)


class ServiceDirective(Directive):
    """ Service directive.

    Injects sections in the documentation about the services registered in the
    given module.

    Usage, in a sphinx documentation::

        .. cornice-autodoc::
            :modules: your.module
            :app: load app to work with imperative add_view call.
            :services: name1, name2
            :service: name1 # no need to specify both services and service.
            :ignore: a comma separated list of services names to ignore
            :ignore-methods: a comma separated list of method names to ignore
            :docstring-replace: replace certain words in docstring
            :title-replace: replace certain words in service title


    """
    has_content = True
    option_spec = {'modules': convert_to_list,
                   'app': directives.unchanged,
                   'service': directives.unchanged,
                   'services': convert_to_list,
                   'ignore': convert_to_list,
                   'ignore-methods': convert_to_list,
                   'docstring-replace': from_json_to_dict,
                   'title-replace': from_json_to_dict}
    domain = 'cornice'
    doc_field_types = []

    # Warning: this might be removed in future version. Don't touch this from extensions.
    _doc_field_type_map: t.Dict[str, t.Tuple[Field, bool]] = {}

    def get_field_type_map(self) -> t.Dict[str, t.Tuple[Field, bool]]:
        if self._doc_field_type_map == {}:
            self._doc_field_type_map = {}
            for field in self.doc_field_types:
                for name in field.names:
                    self._doc_field_type_map[name] = (field, False)

                if field.is_typed:
                    typed_field = t.cast(TypedField, field)
                    for name in typed_field.typenames:
                        self._doc_field_type_map[name] = (field, True)

        return self._doc_field_type_map

    def __init__(self, *args, **kwargs):
        super(ServiceDirective, self).__init__(*args, **kwargs)
        self.env = self.state.document.settings.env

    def run(self):
        app_name = self.options.get('app')
        if app_name:
            app = import_module(app_name)
            app.main({})

        # import the modules, which will populate the SERVICES variable.
        for module in self.options.get('modules', []):
            if module in MODULES:
                reload(MODULES[module])
            else:
                MODULES[module] = import_module(module)

        names = self.options.get('services', [])

        service = self.options.get('service')
        if service is not None:
            names.append(service)

        # filter the services according to the options we got
        services = get_services(names=names or None,
                                exclude=self.options.get('exclude'))

        # clear the SERVICES variable, which will allow to use this
        # directive multiple times
        clear_services()

        return [self._render_service(s) for s in services]

    def _resolve_obj_to_docstring(self, obj, args):
        # Resolve a view or validator to an object if type string
        # and return docstring.
        if is_string(obj):
            if 'klass' in args:
                ob = args['klass']
                obj_ = getattr(ob, obj.lower())
                return format_docstring(obj_)
            else:
                return ''
        else:
            return format_docstring(obj)

    @staticmethod
    def _get_attributes(schema, location):
        """Return the schema's children, filtered by location."""
        schema = DottedNameResolver(__name__).maybe_resolve(schema)

        def _filter(attr):
            if not hasattr(attr, "location"):
                valid_location = 'body' in location
            else:
                valid_location = attr.location in to_list(location)
            return valid_location

        return list(filter(_filter, schema().children))

    def _render_service(self, service):
        service_id = "service-%d" % self.env.new_serialno('service')
        service_node = nodes.section(ids=[service_id])

        title = '%s service at %s' % (service.name.title(), service.path)
        for replace_key, replace_value in self.options.get(
            'title-replace', {}
        ).items():
            title = title.replace(replace_key, replace_value)

        service_node += nodes.title(text=title)

        if service.description is not None:
            service_node += rst2node(trim(service.description), self.env)

        for method, view, args in service.definitions:
            if method == 'HEAD':
                # Skip head - this is essentially duplicating the get docs.
                continue

            if method in self.options.get('ignore-methods', []):
                # Skip ignored methods
                continue

            method_id = '%s-%s' % (service_id, method)
            method_node = nodes.section(ids=[method_id])
            method_node += nodes.title(text=method)

            docstring = self._resolve_obj_to_docstring(view, args)

            for replace_key, replace_value in self.options.get(
                'docstring-replace', {}
            ).items():
                docstring = docstring.replace(replace_key, replace_value)

            if 'schema' in args:
                schema = args['schema']

                attrs_node = nodes.inline()
                for location in ('header', 'querystring', 'body'):
                    attributes = self._get_attributes(schema,
                                                      location=location)
                    if attributes:
                        attrs_node += nodes.inline(
                            text='values in the %s' % location)
                        location_attrs = nodes.bullet_list()

                        for attr in attributes:
                            temp = nodes.list_item()

                            # Get attribute data-type
                            if hasattr(attr, 'type'):
                                attr_type = attr.type
                            elif hasattr(attr, 'typ'):
                                attr_type = attr.typ.__class__.__name__
                            else:
                                attr_type = None

                            temp += nodes.strong(text=attr.name)
                            if attr_type is not None:
                                temp += nodes.inline(text=' (%s)' % attr_type)
                            if not attr.required or attr.description:
                                temp += nodes.inline(text=' - ')
                                if not attr.required:
                                    if attr.missing is not None:
                                        default = json.dumps(attr.missing)
                                        temp += nodes.inline(
                                            text='(default: %s) ' % default)
                                    else:
                                        temp += nodes.inline(
                                            text='(optional) ')
                                if attr.description:
                                    temp += nodes.inline(text=attr.description)

                            location_attrs += temp

                        attrs_node += location_attrs
                method_node += attrs_node

            for validator in args.get('validators', ()):
                docstring += self._resolve_obj_to_docstring(validator, args)

            if 'accept' in args:
                accept = to_list(args['accept'])

                if callable(accept):
                    if accept.__doc__ is not None:
                        docstring += accept.__doc__.strip()
                else:
                    accept_node = nodes.strong(text='Accepted content types:')
                    node_accept_list = nodes.bullet_list()
                    accept_node += node_accept_list

                    for item in accept:
                        temp = nodes.list_item()
                        temp += nodes.inline(text=item)
                        node_accept_list += temp

                    method_node += accept_node

            node = rst2node(docstring, self.env)
            DocFieldTransformer(self).transform_all(node)
            if node is not None:
                method_node += node

            renderer = args['renderer']
            if renderer == 'simplejson':
                renderer = 'json'

            response = nodes.paragraph()

            response += nodes.strong(text='Response: %s' % renderer)
            method_node += response

            service_node += method_node
        return service_node


# Utils

def format_docstring(obj):
    """Return trimmed docstring with newline from object."""
    return trim(obj.__doc__ or "") + '\n'


def trim(docstring):
    """
    Remove the tabs to spaces, and remove the extra spaces / tabs that are in
    front of the text in docstrings.

    Implementation taken from http://www.python.org/dev/peps/pep-0257/
    """
    if not docstring:
        return ''
    # Convert tabs to spaces (following the normal Python rules)
    # and split into a list of lines:
    lines = docstring.expandtabs().splitlines()
    # Determine minimum indentation (first line doesn't count):
    indent = sys.maxsize
    for line in lines[1:]:
        stripped = line.lstrip()
        if stripped:
            indent = min(indent, len(line) - len(stripped))
    # Remove indentation (first line is special):
    trimmed = [lines[0].strip()]
    if indent < sys.maxsize:
        for line in lines[1:]:
            trimmed.append(line[indent:].rstrip())
    # Strip off trailing and leading blank lines:
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    while trimmed and not trimmed[0]:
        trimmed.pop(0)
    # Return a single string:
    res = '\n'.join(trimmed)
    if not PY3 and not isinstance(res, unicode):
        res = res.decode('utf8')
    return res


class _HTMLFragmentTranslator(HTMLTranslator):
    def __init__(self, document):
        HTMLTranslator.__init__(self, document)
        self.head_prefix = ['', '', '', '', '']
        self.body_prefix = []
        self.body_suffix = []
        self.stylesheet = []

    def astext(self):
        return ''.join(self.body)


class _FragmentWriter(Writer):
    translator_class = _HTMLFragmentTranslator

    def apply_template(self):
        subs = self.interpolation_dict()
        return subs['body']


def rst2html(data):
    """Converts a reStructuredText into its HTML
    """
    if not data:
        return ''
    return core.publish_string(data, writer=_FragmentWriter())


def rst2node(data, env):
    """Converts a reStructuredText into its node
    """
    if not data:
        return
    parser = docutils.parsers.rst.Parser()
    document = docutils.utils.new_document('<>')
    document.settings = docutils.frontend.OptionParser().get_default_values()
    document.settings.tab_width = 4
    document.settings.pep_references = False
    document.settings.rfc_references = False
    document.settings.character_level_inline_markup = False
    document.settings.env = env
    parser.parse(data, document)
    if len(document.children) == 1:
        return document.children[0].deepcopy()
    else:
        par = docutils.nodes.paragraph()
        for child in document.children:
            par += child.deepcopy()
        return par


class ExecDirective(Directive):
    """Execute the python code and inserts the output into the document."""
    has_content = True

    def run(self):
        """Main ExecDirective method."""
        oldStdout, sys.stdout = sys.stdout, StringIO()

        tab_width = self.options.get(
            'tab-width', self.state.document.settings.tab_width)
        source = self.state_machine.input_lines.source(
            self.lineno - self.state_machine.input_offset - 1)

        try:
            exec('\n'.join(self.content))
            text = sys.stdout.getvalue()
            lines = statemachine.string2lines(
                text, tab_width, convert_whitespace=True)
            self.state_machine.insert_input(lines, source)
            return []
        except Exception:
            return [nodes.error(
                None,
                nodes.paragraph(
                    text='Unable to execute python code at {}:{}:'.format(
                        basename(source), self.lineno)),
                nodes.paragraph(text=str(sys.exc_info()[1])),
            )]
        finally:
            sys.stdout = oldStdout


def setup(app):
    """Hook the directives when Sphinx ask for it."""
    app.add_directive('services', ServiceDirective)  # deprecated
    app.add_directive('cornice-autodoc', ServiceDirective)
    app.add_directive('exec', ExecDirective)
