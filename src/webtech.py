#!/usr/bin/env python
# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
import json
import re
import random
import os
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
from collections import namedtuple

# TODO: change to a local import
import database

# Disable warning about Insecure SSL
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

Tech = namedtuple('Tech', ['name', 'version'])


def parse_header_string(string):
    """
    Parse header string according to wappalizer DB format

    strings follow the below format:
    <string>[\\;version:\\\d][\\;confidence:\d]

    "string" is a mandatory regex string followed by 0 or more parameters (key:value), can be empty
    parameters are divided by a \\; sequence (a backslash followed by a semicolon)

    examples of parameters are:
    "version": indicate wich regex group store the version information
    "confidence": indicate a rate of confidence
    """
    parts = string.split("\;")
    if len(parts) == 1:
        return parts[0], None
    else:
        extra = {}
        for p in parts[1:]:
            extra[p.split(":")[0]] = p.split(":")[1]
        return parts[0], extra

def get_random_user_agent():
    """
    Get a random user agent from a file
    """

    ua_file = os.path.join(os.path.realpath(os.path.dirname(__file__)), "ua.txt")
    with open(ua_file) as f:
        agents = f.readlines()
        return random.choice(agents).strip()

class WebTech():
    VERSION = 0.1
    USER_AGENT = "webtech/{}".format(VERSION)
    COMMON_HEADERS = ['Accept-Ranges', 'Access-Control-Allow-Methods', 'Access-Control-Allow-Origin', 'Age', 'Cache-Control', 'Connection',
                      'Content-Encoding', 'Content-Length', 'Content-Security-Policy', 'Content-Type', 'Date', 'ETag', 'Expect-CT', 'Expires',
                      'Feature-Policy', 'Keep-Alive', 'Last-Modified', 'Link', 'P3P', 'Pragma', 'Referrer-Policy', 'Set-Cookie',
                      'Strict-Transport-Security', 'Transfer-Encoding', 'Vary', 'X-Cache', 'X-Cache-Hits', 'X-Content-Security-Policy',
                      'X-Content-Type-Options', 'X-Frame-Options', 'X-Timer', 'X-WebKit-CSP', 'X-XSS-Protection']
    COMMON_HEADERS = [ch.lower() for ch in COMMON_HEADERS]

    # 'cats' tech categories
    # 'implies' website is using also this tech
    # 'excludes' exclude this tech
    # 'website' website for this tech
    # 'icon' icon for this tech (useless)

    # 'headers' check this patter in headers
    # 'html' check this regex in html
    # 'meta' check this patter in meta
    # 'js' check this expression in javascript context
    # 'cookies' check this patter in cookies
    # 'script' check this pattern in scripts src
    # 'url' check this patter in url

    def __init__(self, options):
        if options.db_file is not None:
            self.db_file = options.db_file
        else:
            self.db_file = database.DATABASE_FILE
        with open(self.db_file) as f:
            self.db = json.load(f)
        if options.urls is not None:
            self.urls = options.urls
        if options.urls_file is not None:
            with open(options.urls_file) as f:
                self.urls = f.readlines()
        if options.user_agent is not None:
            self.USER_AGENT = options.user_agent
        if options.use_random_user_agent:
            self.USER_AGENT = get_random_user_agent()
        self.output_grep = options.output_grep
        self.output_json = options.output_json

    def start(self):
        """
        Start the engine, fetch an URL and report the findings
        """
        self.header = {'User-Agent': self.USER_AGENT}

        # self.data contains the data fetched from the request
        # this object SHOULD be append-only and immutable after the scraping/whitelist process
        self.data = {
            'url': None,
            'html': None,
            'headers': None,
            'cookies': None,
            'meta': None,
            'script': None
        }

        # self.report contains the information about the technologies detected
        self.report = {
            'tech': set(),
            'headers': [],
        }

        for url in self.urls:
            parsed_url = urlparse(url)
            if "http" in parsed_url.scheme:
                self.scrape_url(url)
            else:
                self.parse_file(url)

            self.whitelist_data()

            # Cycle through all the db technologies and do all the checks
            # It's more efficent cycling all technologies and match against the target once for tech
            # instead of cycling each target feature against every technology
            for tech in self.db["apps"]:
                t = self.db["apps"][tech]
                headers = t.get("headers")
                html = t.get("html")
                meta = t.get("meta")
                cookies = t.get("cookies")
                script = t.get("script")
                url = t.get("url")
                if headers:
                    self.check_headers(tech, headers)
                #if html:
                #    self.check_headers(tech, headers)
                #if meta:
                #    self.check_headers(tech, headers)
                if cookies:
                    self.check_cookies(tech, cookies)
                #if script:
                #    self.check_headers(tech, headers)
                if url:
                    self.check_url(tech, url)

            self.print_report()

    def scrape_url(self, url):
        """
        Scrape the target URL and collects all the data that will be filtered afterwards
        """
        # By default we don't verify SSL certificates, we are only performing some useless GETs
        response = requests.get(url, headers=self.header, verify=False)
        print("status: {}".format(response.status_code))

        # TODO: switch-case for various response.status_code

        self.data['url'] = url
        self.data['html'] = response.text #.replace('\n','')
        self.data['headers'] = response.headers
        print(self.data['headers'])
        self.data['cookies'] = requests.utils.dict_from_cookiejar(response.cookies)
        print(self.data['cookies'])

        self.parse_html_page()

        print(self.data)

    def parse_file(self, path):
        """
        Parse an HTTP response file and collects all the data that will be filtered afterwards

        TODO: find a better way to do this :(
        """
        try:
            path = path.replace('file://','')
            response = open(path).read().replace('\r\n','\n')
        except FileNotFoundError:
            print("Cannot open file {}, is it a file?".format(path))
            print("Trying with {}...".format("https://" + path))
            return self.scrape_url("https://" + path)
        self.data['url'] = path

        headers_raw = response.split('\n\n', 1)[0]
        parsed_headers = requests.structures.CaseInsensitiveDict()
        for header in headers_raw.split('\n'):
            # might be first row: HTTP/1.1 200
            if ":" not in header:
                continue
            if "set-cookie" not in header.lower():
                header_name = header.split(':',1)[0].strip()
                header_value = header.split(':',1)[1].strip()
                parsed_headers[header_name] = header_value
        self.data['headers'] = parsed_headers

        self.data['html'] = response.split('\n\n', 1)[1]

        self.data['cookies'] = {}
        if "set-cookie:" in headers_raw.lower():
            for header in headers_raw.split("\n"):
                if "set-cookie:" in header.lower():
                    # 'Set-Cookie: dr=gonzo; path=/trmon' -> "dr"
                    cookie_name = header.split('=',1)[0].split(':')[1].strip()
                    # 'Set-Cookie: dr=gonzo; domain=jolla.it;' -> "gonzo"
                    cookie_value = header.split('=',1)[1].split(';',1)[0].strip()
                    # BUG: if there are cookies for different domains with the same name
                    # they are going to be overwritten (last occurrence will last)...
                    # ¯\_(ツ)_/¯
                    self.data['cookies'][cookie_name] = cookie_value

        self.parse_html_page()

        print(self.data)

    def parse_html_page(self):
        """
        Parse HTML content to get meta tag and script-src
        """
        soup = BeautifulSoup(self.data['html'], 'html.parser')
        #print(soup)

        # html meta tags
        self.data['meta'] = [meta for meta in soup.findAll("meta")]
        # html script-src links
        self.data['script'] = [script.get('src') for script in soup.findAll("script", {"src": True})]

    def whitelist_data(self):
        """
        Whitelist collected data to report the important/uncommon data BEFORE matching with the database

        This function is useful for CMS/technologies that are not in the database
        """
        for header in self.data['headers']:
            # This is pretty stupid and need an alternative approach
            if header.lower() not in self.COMMON_HEADERS:
                self.report['headers'].append(header)

    def check_html(self, tech, html):
        """
        Check if request html contains some database matches 
        """

    def check_headers(self, tech, headers):
        """
        Check if request headers match some database headers
        """
        # For every tech header check if there is a match in our target
        for header in headers:
            try:
                # _store, hacky way to get the original key from a request.structures.CaseInsensitiveDict
                real_header, content = self.data['headers']._store[header.lower()]
            except KeyError:
                # tech header not found, go ahead
                continue
            # Parse the matching regex
            attr, extra = parse_header_string(headers[header])
            matches = re.search(attr, content, re.IGNORECASE)
            # Attr is empty for a "generic" tech header
            if attr is '' or matches is not None:
                matched_tech = Tech(name=tech, version=None)
                # Remove the matched header from the Custom Header list
                try:
                    self.report['headers'].remove(real_header)
                except ValueError:
                    pass
                # The version extra data is present
                if extra and extra['version']:
                    if matches.group(1):
                        matched_tech = matched_tech._replace(version=matches.group(1))
                self.report['tech'].add(matched_tech)

    def check_cookies(self, tech, cookies):
        """
        Check if request cookies match some database cookies
        """
        for cookie in cookies:
            # cookies in db are regexes so we must test them all
            cookie = cookie.replace("*","") # FIX for "Fe26.2**" hapi.js cookie
            for biscuit in self.data['cookies'].keys():
                matches = re.search(cookie, biscuit, re.IGNORECASE)
                if matches is not None:
                    # TODO: check if cookie content matches. For now we don't care
                    #content = self.data['cookies'][biscuit]
                    matched_tech = Tech(name=tech, version=None)
                    self.report['tech'].add(matched_tech)

    def check_url(self, tech, url):
        """
        Check if request url match some database url rules
        """
        matches = re.search(url, self.data['url'], re.IGNORECASE)
        if matches is not None:
            matched_tech = Tech(name=tech, version=None)
            self.report['tech'].add(matched_tech)

    def print_report(self):
        """
        Print a report
        """
        if self.output_grep:
            # TODO
            print("TODO")
        elif self.output_json:
            print(json.dumps(self.report, sort_keys=True, indent=4))
        else:
            print("Target URL: {}".format(self.data['url']))
            if self.report['tech']:
                print("Detected technologies:")
                for tech in self.report['tech']:
                    print("\t- {} {}".format(tech.name, '' if tech.version is None else tech.version))

            if self.report['headers']:
                print("Detected the following interesting custom headers:")
                for header in self.report['headers']:
                    print("\t- {}: {}".format(header, self.data['headers'].get(header)))