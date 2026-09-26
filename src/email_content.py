"""Render email links explicitly instead of relying on mail-client auto-linking."""
from html import escape
from html.parser import HTMLParser


class _LinkRenderer(HTMLParser):
    def __init__(self, link):
        super().__init__(convert_charrefs=False)
        self.link = escape(link, quote=True)
        self.parts = []
        self.anchors = 0
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        self.parts.append(self.get_starttag_text().replace('{link}', self.link))
        if tag == 'a':
            self.anchors += 1
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_startendtag(self, tag, attrs):
        self.parts.append(self.get_starttag_text().replace('{link}', self.link))

    def handle_endtag(self, tag):
        self.parts.append(f'</{tag}>')
        if tag == 'a':
            self.anchors = max(0, self.anchors - 1)
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        replacement = self.link
        if not self.anchors and not self.hidden:
            replacement = f'<a href="{self.link}">{self.link}</a>'
        self.parts.append(data.replace('{link}', replacement))

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def handle_comment(self, data):
        self.parts.append(f'<!--{data}-->')

    def handle_decl(self, decl):
        self.parts.append(f'<!{decl}>')


def render_email_html(template, link):
    renderer = _LinkRenderer(link)
    renderer.feed(template)
    renderer.close()
    return ''.join(renderer.parts)
