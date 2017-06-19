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
       
        FONT_CHARACTER_TABLES = {}
        
        for font_file in glob('{0}/fonts/*.ttf'.format(TEMPLATE_DIR)):

           font_name = os.path.basename(os.path.splitext(font_file)[0])
           pdfmetrics.registerFont(TTFont(font_name, font_file))


        styleTrebBold = ParagraphStyle(
           name="trebuchetB", leading=10,
           fontName='Trebuchet-Bold'
        )

        styleTrebRegular = ParagraphStyle(
           name="trebuchetR", leading=10,
           fontName='Trebuchet-Regular'
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

        RIGHT_INDENT = 40  # mm from the right side for the CERTIFICATE


        #  Student name

        # default is to use the DejaVu font for the name,
        # will fall back to Arial if there are
        # unusual characters
        style = styleTrebBold
        style.leading = 20
        style.fontSize = 18


        len_name = len(student_name)

        style.textColor = colors.Color(
           0, 0.674, 0.843)
        style.alignment = TA_LEFT

        paragraph_string = u"{0}".format(
           student_name.decode('utf-8'))
        paragraph = Paragraph(paragraph_string, style)

        if len_name > 59: #va a capo
            paragraph.wrapOn(c, 181 * mm, 150 * mm)
            paragraph.drawOn(c, 110 * mm, 125 * mm)
        else:
            paragraph.wrapOn(c, (WIDTH - RIGHT_INDENT) * mm, HEIGHT * mm)
            paragraph.drawOn(c, 110 * mm, 131 * mm)



        # Course name
        len_name = len(self.long_course)
        style.fontSize = 18
        
        style.textColor = colors.Color(
           0, 0.674, 0.843)
        style.alignment = TA_LEFT

        paragraph_string = "{0}".format(
           self.long_course.decode('utf-8'))
        paragraph = Paragraph(paragraph_string, style)

        if len_name > 59: #va a capo
            paragraph.wrapOn(c, 181 * mm, 150 * mm)
            paragraph.drawOn(c, 110 * mm, 106 * mm)
        else:
            paragraph.wrapOn(c, (WIDTH - RIGHT_INDENT) * mm, HEIGHT * mm)
            paragraph.drawOn(c, 110 * mm, 113 * mm)


        # issue date
        style = styleTrebRegular
        style.fontSize = 9
        style.leading = 10
        style.textColor = colors.Color(
           0.345, 0.341, 0.329)
        style.alignment = TA_LEFT

        paragraph_string = "{0}".format(self.issued_date)
        paragraph = Paragraph("{0}".format(
        paragraph_string), style)
        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, 110 * mm, 78 * mm)

        

        # Honor code
        #style = styleTrebBold
        style.fontSize = 7
        style.leading = 10
        style.textColor = colors.Color(
           0.345, 0.341, 0.329)
        style.alignment = TA_LEFT

        paragraph_string = "Authenticity of this certificate can be verified at " \
                           "<a href='{verify_url}/{verify_path}/{verify_uuid}'>" \
                           "{verify_url}/{verify_path}/{verify_uuid}</a>"

        paragraph_string = paragraph_string.format(
           verify_url=settings.CERT_VERIFY_URL,
           verify_path=S3_VERIFY_PATH,
           verify_uuid=verify_uuid
        )
        paragraph = Paragraph(paragraph_string, style)

        paragraph.wrapOn(c, WIDTH * mm, HEIGHT * mm)
        paragraph.drawOn(c, 110 * mm, 27 * mm)  

        


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
