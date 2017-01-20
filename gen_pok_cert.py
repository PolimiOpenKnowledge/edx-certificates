# -*- coding: utf-8 -*-

import copy
import datetime
import gnupg
import math
import os
import re
import shutil
import StringIO
import uuid

from reportlab.platypus import Paragraph
from PyPDF2 import PdfFileWriter, PdfFileReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4, letter, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from glob import glob
from HTMLParser import HTMLParser

import settings
import collections
import itertools
import logging.config
import reportlab.rl_config
import tempfile
import boto.s3
from boto.s3.key import Key
from bidi.algorithm import get_display
import arabic_reshaper

from gen_cert import CertificateGen, S3_CERT_PATH, TARGET_FILENAME, TMP_GEN_DIR, S3_VERIFY_PATH, TEMPLATE_DIR
from opaque_keys.edx.keys import CourseKey

reportlab.rl_config.warnOnMissingFontGlyphs = 0


class PokCertificateGen(CertificateGen):
    """Extends CertificateGen with pok certificate generator method"""

    def __init__(self, course_id, template_pdf=None, aws_id=None, aws_key=None,
                 dir_prefix=None, long_org=None, long_course=None, issued_date=None):
        """Initialize as super
        """
        super(PokCertificateGen, self).__init__(course_id, template_pdf, aws_id, aws_key,
                                                dir_prefix, long_org, long_course, issued_date)
        self.cert_language = self.cert_data.get('CERT_LANGUAGE', 'IT')


    def delete_certificate(self, delete_download_uuid, delete_verify_uuid):
        # TODO remove/archive an existing certificate
        raise NotImplementedError

    def _generate_certificate(
        self,
        student_name,
        download_dir,
        verify_dir,
        filename=TARGET_FILENAME,
        grade=None,
        designation=None,
    ):
        """Generate a certificate PDF, signature and validation html files.

        return (download_uuid, verify_uuid, download_url)
        """
        versionmap = {
            1: self._generate_v1_certificate,
            2: self._generate_v2_certificate,
            'MIT_PE': self._generate_mit_pe_certificate,
            'stanford': self._generate_stanford_SOA,
            '3_dynamic': self._generate_v3_dynamic_certificate,
            'stanford_cme': self._generate_stanford_cme_certificate,
            'POK': self._generate_pok_certificate
        }
        # TODO: we should be taking args, kwargs, and passing those on to our callees
        return versionmap[self.template_version](
            student_name,
            download_dir,
            verify_dir,
            filename,
            grade,
            designation,
        )

    def _generate_pok_certificate(
           self,
           student_name,
           download_dir,
           verify_dir,
           filename=TARGET_FILENAME,
           grade=None,
           designation=None,
           generate_date=None,
       ):
        """
        Generate the POK certs
        """
        # A4 page size is 297mm x 210mm
        cert_language=self.cert_language
        verify_uuid = uuid.uuid4().hex
        download_uuid = uuid.uuid4().hex
        download_url = "{base_url}/{cert}/{uuid}/{file}".format(
           base_url=settings.CERT_DOWNLOAD_URL,
           cert=S3_CERT_PATH, uuid=download_uuid, file=filename
        )
        filename = os.path.join(download_dir, download_uuid, filename)

        # This file is overlaid on the template certificate
        overlay_pdf_buffer = StringIO.StringIO()
        c = canvas.Canvas(overlay_pdf_buffer)
        c.setPageSize((297 * mm, 210 * mm))

        # register all fonts in the fonts/ dir,
        # there are more fonts in here than we need
        # but the performance hit seems minimal

        # for font_file in glob('{0}/fonts/*.ttf'.format(self.template_dir)):
        #    font_name = os.path.basename(os.path.splitext(font_file)[0])
        #    pdfmetrics.registerFont(TTFont(font_name, font_file))

        # 0 0 - normal
        # 0 1 - italic
        # 1 0 - bold
        # 1 1 - italic and bold

        addMapping('OpenSans-Light', 0, 0, 'OpenSans-Light')
        addMapping('OpenSans-Light', 0, 1, 'OpenSans-LightItalic')
        addMapping('OpenSans-Light', 1, 0, 'OpenSans-Bold')

        addMapping('OpenSans-Regular', 0, 0, 'OpenSans-Regular')
        addMapping('OpenSans-Regular', 0, 1, 'OpenSans-Italic')
        addMapping('OpenSans-Regular', 1, 0, 'OpenSans-Bold')
        addMapping('OpenSans-Regular', 1, 1, 'OpenSans-BoldItalic')

        styleArial = ParagraphStyle(
           name="arial", leading=10,
           fontName='Arial Unicode'
        )
        styleOpenSans = ParagraphStyle(
           name="opensans-regular", leading=10,
           fontName='OpenSans-Regular'
        )
        styleOpenSansLight = ParagraphStyle(
           name="opensans-light", leading=10,
           fontName='OpenSans-Light'
        )

        # Text is overlayed top to bottom
        #   * Issued date (top right corner)
        #   * "This is to certify that"
        #   * Student's name
        #   * "successfully completed"
        #   * Course name
        #   * "a course of study.."
        #   * honor code url at the bottom
        WIDTH = 297  # width in mm (A4)
        HEIGHT = 210  # hight in mm (A4)

        LEFT_INDENT = 49  # mm from the left side to write the text
        RIGHT_INDENT = 49  # mm from the right side for the CERTIFICATE

        # CERTIFICATE

        styleOpenSansLight.fontSize = 19
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
           0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "CERTIFICATE" if cert_language == 'EN' else "ATTESTATO DI PARTECIPAZIONE"

        # Right justified so we compute the width
        width = stringWidth(
           paragraph_string,
           'OpenSans-Light', 19) / mm
        paragraph = Paragraph("{0}".format(
           paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 180 * mm)   # 180 con small, 200 con big
        # Issued ..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
           0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "{0}".format(self.issued_date)

        # Right justified so we compute the width
        width = stringWidth(
           paragraph_string,
           'OpenSans-LightItalic', 12) / mm
        paragraph = Paragraph("<i>{0}</i>".format(
           paragraph_string), styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, (WIDTH - RIGHT_INDENT - width) * mm, 170 * mm)  # 170 con small, 190 con big

        # This is to certify..

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
           0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = "This is to certify that" if cert_language == 'EN' else "Si attesta che"
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 132.5 * mm)

        #  Student name

        # default is to use the DejaVu font for the name,
        # will fall back to Arial if there are
        # unusual characters
        style = styleOpenSans
        style.leading = 10
        width = stringWidth(
           student_name.decode('utf-8'),
           'OpenSans-Bold', 34) / mm
        paragraph_string = "<b>{0}</b>".format(student_name)

        if self._use_unicode_font(student_name):
            style = styleArial
            width = stringWidth(student_name.decode('utf-8'),
                                'Arial Unicode', 34) / mm
            # There is no bold styling for Arial :(
            paragraph_string = "{0}".format(student_name)

        # We will wrap at 200mm in, so if we reach the end (200-47)
        # decrease the font size
        if width > 153:
            style.fontSize = 18
            nameYOffset = 121.5
        else:
            style.fontSize = 34
            nameYOffset = 124.5

        style.textColor = colors.Color(
           0, 0.624, 0.886)
        style.alignment = TA_LEFT

        paragraph = Paragraph(paragraph_string, style)
        paragraph.wrapOn(c, 200 * mm, 214 * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, nameYOffset * mm)

        # Successfully completed

        styleOpenSansLight.fontSize = 12
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
           0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_LEFT

        paragraph_string = ""
        if cert_language == 'EN':
            paragraph_string = "successfully completed the course:"
        else:
            paragraph_string = "ha partecipato con successo al corso:"

        paragraph = Paragraph(paragraph_string, styleOpenSansLight)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, LEFT_INDENT * mm, 108 * mm)

        # Course name
        len_name = len(self.long_course)

        # styleOpenSans.fontName = 'OpenSans-BoldItalic'
        if len_name > 50:
            styleOpenSans.fontSize = 24
            styleOpenSans.leading = 21
        else:
            styleOpenSans.fontSize = 24
            styleOpenSans.leading = 10
        styleOpenSans.textColor = colors.Color(
           0, 0.624, 0.886)
        styleOpenSans.alignment = TA_LEFT

        paragraph_string = u"<b><i>{0}</i></b>".format(
           self.long_course.decode('utf-8'))
        # "<b><i>{0}: {1}</i></b>".format(self.course, self.long_course)
        paragraph = Paragraph(paragraph_string, styleOpenSans)
        # paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        if len_name > 50:
            paragraph.wrapOn(c, 200 * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, 88 * mm)
        else:
            paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
            paragraph.drawOn(c, LEFT_INDENT * mm, 99 * mm)

        # Honor code

        styleOpenSansLight.fontSize = 7
        styleOpenSansLight.leading = 10
        styleOpenSansLight.textColor = colors.Color(
           0.302, 0.306, 0.318)
        styleOpenSansLight.alignment = TA_CENTER

        paragraph_string = "HONOR CODE CERTIFICATE<br/>" \
                           "*Authenticity of this certificate can be verified at " \
                           "<a href='{verify_url}/{verify_path}/{verify_uuid}'>" \
                           "{verify_url}/{verify_path}/{verify_uuid}</a>"

        paragraph_string = paragraph_string.format(
           verify_url=settings.CERT_VERIFY_URL,
           verify_path=S3_VERIFY_PATH,
           verify_uuid=verify_uuid
        )
        paragraph = Paragraph(paragraph_string, styleOpenSansLight)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, 0 * mm, 24 * mm)   # 24 con template small , 5 con big

        ########

        c.showPage()
        c.save()

        # Merge the overlay with the template, then write it to file
        output = PdfFileWriter()
        overlay = PdfFileReader(overlay_pdf_buffer)

        # We need a page to overlay on.
        # So that we don't have to open the template
        # several times, we open a blank pdf several times instead
        # (much faster)

        blank_pdf = PdfFileReader(
           file("{0}/blank.pdf".format(TEMPLATE_DIR), "rb")
        )
        print "pdf.documentInfo: " + str(self.template_pdf.documentInfo)

        final_certificate = blank_pdf.getPage(0)
        final_certificate.mergePage(self.template_pdf.getPage(0))
        final_certificate.mergePage(overlay.getPage(0))

        output.addPage(final_certificate)

        self._ensure_dir(filename)

        outputStream = file(filename, "wb")
        output.write(outputStream)
        outputStream.close()

        self._generate_verification_page(
           student_name,
           filename,
           verify_dir,
           verify_uuid,
           download_url
        )

        return (download_uuid, verify_uuid, download_url)
