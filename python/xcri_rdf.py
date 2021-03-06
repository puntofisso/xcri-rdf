import datetime
import itertools
import re
try:
    from cStringIO import StringIO
except ImportError:
    import StringIO
from xml.sax.saxutils import XMLGenerator

from lxml import etree
import rdflib

XMLNS = {
    'xcri': 'http://xcri.org/profiles/1.2/catalog',
    'mlo': 'http://purl.org/net/mlo',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance'
}
INVERSE_XMLNS = tuple((v, k) for k, v in sorted(XMLNS.items(), key=lambda (k,v): -len(v)))

NS = {
    'skos': 'http://www.w3.org/2004/02/skos/core#',
    'foaf': 'http://xmlns.com/foaf/0.1/',
#    'xcri': 'http://xcri.org/profiles/1.2/catalog/',
    'xcri': 'http://xcri.org/profiles/1.2/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'mlo': 'http://purl.org/net/mlo/',
    'xtypes': 'http://purl.org/xtypes/',
    'v': 'http://www.w3.org/2006/vcard/ns#',
}
class _NS(dict):
    def __init__(self, ns):
        super(_NS, self).__init__((prefix, rdflib.Namespace(uri)) for prefix, uri in ns.iteritems())
    def __getattr__(self, name):
        return self[name]
NS = _NS(NS)

is_localpart = re.compile(u"""^[A-Z _ a-z \xc0-\xd6 \xd8-\xf6 \xf8-\xff \u037f-\u1fff \u200c-\u218f]
                               [A-Z _ a-z \xc0-\xd6 \xd8-\xf6 \xf8-\xff \u037f-\u1fff \u200c-\u218f \\- . \\d]*$""",
                          re.VERBOSE).match

def xsi_type(uri):
    for ns, prefix in INVERSE_XMLNS:
        if uri.startswith(ns):
            localpart = uri[len(ns):]
            if is_localpart(localpart):
                return {'xsi:type': "%s:%s" % (prefix, localpart)}
    for i in xrange(len(uri)):
        if is_localpart(uri[i:]):
            return {'xsi:type' :'ns:%s' % uri[i:], 'xmlns:ns': uri[:i]}
    return {}

identifiers = (NS.dc.identifier, NS.dcterms.identifier)
subjects = (NS.dcterms.subject, NS.dc.subject)
labels = (NS.skos.prefLabel, NS.rdfs.label, NS.dcterms.title, NS.dc.title)
urls = (NS.foaf.homepage, NS.foaf.page)
descriptions = (NS.dcterms.description, NS.rdfs.comment)

class IndentingXMLGenerator(XMLGenerator):
    def __init__(self, *args, **kwargs):
        XMLGenerator.__init__(self, *args, **kwargs)
        self.depth = 0
        self.chars = False

    def startElement(self, name, attrs):
        if self.depth and not self.chars:
            XMLGenerator.characters(self, '\n' + '  '*self.depth)
        XMLGenerator.startElement(self, name, attrs)
        self.depth += 1
        self.chars = False

    def endElement(self, name):
        self.depth -= 1
        if not self.chars:
            self.characters('\n' + '  '*self.depth)
        XMLGenerator.endElement(self, name)
        self.chars = False

    def characters(self, content):
        XMLGenerator.characters(self, content)
        self.chars = True

    def textualElement(self, name, attrs, content):
        self.startElement(name, attrs)
        self.characters(content)
        self.endElement(name)

def serialize_etree(xml, xg, previous_nsmap={}):
    attrib = dict(xml.attrib)
    for ns in xml.nsmap:
        if xml.nsmap[ns] == previous_nsmap.get(ns):
            continue
        if ns is None:
            attrib['xmlns'] = xml.nsmap[ns]
        else:
            attrib['xmlns:%s' % ns] = xml.nsmap[ns]
    if xml.prefix is None:
        tag = xml.tag.split('}', 1)[-1]
    else:
        tag = '%s:%s' % (xml.prefix, xml.tag.split('}', 1)[1])
    xg.startElement(tag, attrib)

    if xml.text:
        xg.characters(xml.text)
    for child in xml:
        serialize_etree(child, xg, xml.nsmap)
        if child.tail:
            xg.characters(child.tail)
    xg.endElement(tag)

class XCRICAPSerializer(object):
    common_descriptive_elements = [
        (NS.xcri.abstract, 'xcri:abstract'),
        (NS.xcri.applicationProcedure, 'xcri:applicationProcedure'),
        (NS.mlo.assessment, 'mlo:assessment'),
        (NS.xcri.learningOutcome, 'xcri:learningOutcome'),
        (NS.mlo.objective, 'mlo:objective'),
        (NS.mlo.prerequisite, 'mlo:prerequisite'),
        (NS.xcri.regulations, 'xcri:regulations'),
    ]

    address_elements = [
        (NS.v['street-address'], 'mlo:address'),
        (NS.v['extended-address'], 'mlo:address'),
        (NS.v['locality'], 'mlo:address'),
        (NS.v['postal-code'], 'mlo:postcode'),
    ]

    xsi_schema_locations = {
        "http://xcri.org/profiles/1.2/catalog": "http://www.xcri.co.uk/bindings/xcri_cap_1_2.xsd",
        "http://xcri.org/profiles/1.2/catalog/terms": "http://www.xcri.co.uk/bindings/xcri_cap_terms_1_2.xsd",
        "http://xcri.co.uk": "http://www.xcri.co.uk/bindings/coursedataprogramme.xsd",
        "http://www.w3.org/2003/01/geo/wgs84_pos": "http://xcri-dev.conted.ox.ac.uk/geo.xsd",
    }

    controlled_vocabularies = [
        (NS.xcri.studyMode, 'xcri:studyMode', rdflib.URIRef('http://xcri.org/profiles/catalog/1.2/studyMode/notation')),
        (NS.xcri.attendanceMode, 'xcri:attendanceMode', rdflib.URIRef('http://xcri.org/profiles/catalog/1.2/attendanceMode/notation')),
        (NS.xcri.attendancePattern, 'xcri:attendancePattern', rdflib.URIRef('http://xcri.org/profiles/catalog/1.2/attendancePattern/notation')),
    ]

    def __init__(self, graph, encoding='utf-8'):
        self.graph = graph
        self.catalog = self.graph.value(None, NS.rdf.type, NS.xcri.catalog, any=False)
        self.encoding = encoding

    def serialize(self, stream):
        self.stream = stream
        for _ in self._serialize():
            pass

    def generator(self):
        self.stream = StringIO()
        for _ in self._serialize():
            yield self.stream.getvalue()
            self.stream.seek(0)
            self.stream.truncate()
        yield self.stream.getvalue()
    
    def _serialize(self):
        xg = IndentingXMLGenerator(self.stream, self.encoding)
        xg.startDocument()
        for _ in self.serialize_catalog(xg, self.catalog): yield
        xg.endDocument()

    def serialize_catalog(self, xg, catalog):
        attrib = {}
        attrib.update(('xmlns:%s' % prefix, uri) for prefix, uri in XMLNS.iteritems())
        attrib['xsi:schemaLocation'] = ' '.join(map(' '.join, self.xsi_schema_locations.iteritems()))
        attrib['generated'] = datetime.datetime.utcnow().isoformat() + '+00:00'

        xg.startElement('xcri:catalog', attrib)
        self.serialize_common(xg, catalog)

        provider = self.graph.value(catalog, NS.dcterms.publisher)
        for _ in self.serialize_provider(xg, catalog, provider): yield

        xg.endElement('xcri:catalog')

    def serialize_provider(self, xg, catalog, provider):
        xg.startElement('xcri:provider', {})
        if provider:
            self.serialize_common(xg, provider)
            self.serialize_location(xg, provider)
        yield
        for course in self.graph.objects(catalog, NS.skos.member):
            if (course, NS.rdf.type, NS.xcri.course) not in self.graph:
                continue
            for _ in self.serialize_course(xg, course): yield
        xg.endElement('xcri:provider')
        yield

    def serialize_course(self, xg, course):
        xg.startElement('xcri:course', {})
        self.serialize_common(xg, course)
        self.serialize_common_descriptive_elements(xg, course)
        self.serialize_subjects(xg, course)
        for presentation in self.graph.objects(course, NS.mlo.specifies):
            for _ in self.serialize_presentation(xg, presentation): yield
        xg.endElement('xcri:course')
        yield

    def serialize_presentation(self, xg, presentation):
        xg.startElement('xcri:presentation', {})
        self.serialize_common(xg, presentation)
        self.serialize_common_descriptive_elements(xg, presentation)
        self.serialize_date(xg, presentation, NS.mlo.start, 'mlo:start')
        self.serialize_date(xg, presentation, NS.xcri.end, 'xcri:end')
        self.serialize_places(xg, presentation)
        self.serialize_applyTo(xg, presentation)
        self.serialize_controlled_vocabularies(xg, presentation)
        for venue in self.graph.objects(presentation, NS.xcri.venue):
            for _ in self.serialize_venue(xg, venue): yield
        xg.endElement('xcri:presentation')
        yield

    def serialize_venue(self, xg, venue):
        xg.startElement('xcri:venue', {})
        xg.startElement('xcri:provider', {})
        self.serialize_common(xg, venue)
        self.serialize_location(xg, venue)
        xg.endElement('xcri:provider')
        xg.endElement('xcri:venue')
        yield

    def serialize_common(self, xg, entity):
        self.serialize_identifiers(xg, entity)
        self.serialize_title(xg, entity)
        self.serialize_url(xg, entity)
        self.serialize_description(xg, entity)

    def serialize_location(self, xg, entity):
        xg.startElement('mlo:location', {})
        address = self.graph.value(entity, NS.v.adr)
        if address:
            for prop, name in self.address_elements:
                obj = self.graph.value(address, prop)
                if obj:
                    xg.textualElement(name, {}, unicode(obj))

        xg.endElement('mlo:location')

    def serialize_controlled_vocabularies(self, xg, presentation):
        for prop, name, datatype in self.controlled_vocabularies:
            value = self.graph.value(presentation, prop)
            if not value:
                continue
            # Only use notations with the correct datatype (i.e., scheme)
            for notation in self.graph.objects(value, NS.skos.notation):
                if notation.datatype == datatype:
                    break
            else:
                notation = None
            for label in labels:
                content = self.graph.value(value, label)
                if content:
                    break
            if not (notation or content):
                continue
            xg.textualElement(name,
                              {'identifier': unicode(notation)} if notation else {},
                              content or '')
        

    def serialize_subjects(self, xg, course):
        for obj in itertools.chain(*(self.graph.objects(course, p) for p in subjects)):
            if isinstance(obj, rdflib.Literal):
                attrib, content = {}, unicode(obj)
            else:
                notation = self.graph.value(obj, NS.skos.notation)
                attrib = xsi_type(notation.datatype) if notation else {}
                if notation:
                    attrib['identifier'] = unicode(notation)
                for label in labels:
                    content = self.graph.value(obj, label)
                    if content:
                        break
            if attrib or content:
                xg.textualElement('dc:subject', attrib, content or '')

    def serialize_date(self, xg, entity, prop, name):
        dt = self.graph.value(entity, prop, any=False)
        if isinstance(dt, rdflib.Literal):
            dtf, content = dt.toPython(), None
            if isinstance(dtf, basestring):
                dtf, content = None, dtf
            if not isinstance(dtf, (datetime.datetime, datetime.date)):
                dtf = None
        else:
            dtf = self.graph.value(entity, NS.rdf.value)
            for label in labels:
                content = self.graph.value(dt, label)
                if content:
                    break
        if not (dtf or content):
            return
        attrib = {'dtf': dtf.isoformat()} if dtf else {}
        xg.textualElement(name, attrib, content or '')

    def serialize_identifiers(self, xg, entity):
        for obj in self.graph.objects(entity, NS.skos.notation):
            attrib = xsi_type(obj.datatype)
            if not attrib:
                continue
            xg.textualElement('dc:identifier', attrib, unicode(obj))

        plain_identifiers = set()
        if isinstance(entity, rdflib.URIRef):
            plain_identifiers.add(unicode(entity))
        for obj in itertools.chain(*(self.graph.objects(entity, p) for p in identifiers)):
            if isinstance(obj, rdflib.Literal):
                plain_identifiers.add(unicode(obj))
        for identifier in plain_identifiers:
            xg.textualElement('dc:identifier', {}, identifier)

    def serialize_common_descriptive_elements(self, xg, entity):
        for prop, name in self.common_descriptive_elements:
            for obj in self.graph.objects(entity, prop):
                self.descriptive_text_element(xg, name, obj)

    def descriptive_text_element(self, xg, name, obj):
        """
        Serializes RDF terms that are either links, HTML or plain text.
        """
        if isinstance(obj, rdflib.URIRef):
            xg.startElement(name, {'href': unicode(obj)})
            xg.endElement(name)
        else:
            if obj.datatype == NS.xtypes['Fragment-XHTML']:
                xml = etree.fromstring(unicode(obj))
            elif obj.datatype == NS.xtypes['Fragment-HTML']:
                xml = etree.fromstring(unicode(obj), parser=etree.HTMLParser())
            else:
                xg.textualElement(name, {}, unicode(obj))
                return
            xg.startElement(name, {})
            serialize_etree(xml, xg)
            xg.endElement(name)

    def _find_first(name, ps, rich_element=False):
        def f(self, xg, entity):
            for obj in itertools.chain(*(self.graph.objects(entity, p) for p in ps)):
                if rich_element:
                    self.descriptive_text_element(xg, name, obj)
                else:
                    xg.textualElement(name, {}, unicode(obj))
                break
        return f

    serialize_title = _find_first('dc:title', labels)
    serialize_url = _find_first('mlo:url', urls)
    serialize_description = _find_first('dc:description', descriptions, rich_element=True)
    serialize_places = _find_first('mlo:places', (NS.mlo.places,))
    serialize_applyTo = _find_first('xcri:applyTo', (NS.xcri.applyTo,))

   


if __name__ == '__main__':
    import sys

    graph = rdflib.ConjunctiveGraph()
    if len(sys.argv) > 1:
        for name in sys.argv[1:]:
            with open(name, 'r') as f:
                graph.parse(f)
    else:
        graph.parse(sys.stdin)

    serializer = XCRICAPSerializer(graph)
    serializer.serialize(sys.stdout)
