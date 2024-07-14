#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
import copy
import email
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import Parser as EmailParser
from email.policy import default as default_email_policy
import getpass
import importlib
import mailbox
import os
from pathlib import Path
import re
import shutil
import smtplib
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, cast
import uuid
import webbrowser

import bs4


if transforms_dir := os.environ.get("WP_TRANSFORMS_DIR"):
    transforms_dir = str(Path(transforms_dir).resolve())
    sys.path.append(transforms_dir)
    transforms = importlib.import_module("winds_pleasure_transforms")
else:
    transforms = transforms_dir = None


class WindsPleasure:
    def __init__(
        self, spool: mailbox.Mailbox, sql: sqlite3.Connection, smtp: smtplib.SMTP
    ):
        self.spool = spool
        self.sql = sql
        self.smtp = smtp

    def init_schema(self):
        self.sql.executescript(
            """
          CREATE TABLE IF NOT EXISTS emails (
            original TEXT NOT NULL,
            modified TEXT NOT NULL
          );
        """
        )
        self.sql.commit()

    @contextmanager
    def recording_email(self, original: mailbox.Message, modified: mailbox.Message):
        try:
            self.sql.execute(
                """
              INSERT INTO emails (original, modified)
              VALUES (?, ?)
            """,
                (original.as_string(), modified.as_string()),
            )
            yield
            self.sql.commit()
        finally:
            self.sql.rollback()

    def run(self):
        self.init_schema()
        for key, original_mail in self.spool.items():
            print(
                f"Processing mail from {original_mail['from']} with subject: {original_mail['subject']}"
            )
            modified_mail = self.transform(original_mail)
            with self.recording_email(original_mail, modified_mail):
                self.resend(modified_mail)
                self.spool.remove(key)

    def _get_from_addr(self, mail: mailbox.Message):
        username, domain = mail["delivered-to"].split("@")
        from_addr = cast(Any, mail["from"])
        return str(
            Address(
                display_name=from_addr.addresses[0].display_name + " via YALB Despam",
                username=username,
                domain=domain,
            )
        )

    def _get_message_id(self, mail: mailbox.Message):
        username, orig_domain = re.sub(r"[<>]", "", mail["message-id"]).split("@")
        from_addr = cast(Any, mail["from"])
        new_domain = from_addr.addresses[0].domain
        return str(Address(username=username, domain=new_domain))

    def transform(self, mail: mailbox.Message):
        if not transforms_dir:
            raise RuntimeError("Transforms directory not specified, cannot process")
        old_html = mail.get_body().get_payload(decode=True).decode()
        soup = bs4.BeautifulSoup(old_html, "html.parser")
        for attr in dir(transforms):
            if not attr.startswith("wp_"):
                continue
            transform = getattr(transforms, attr)
            if not callable(transform):
                continue
            if new_soup := transform(soup, mail):
                soup = new_soup
        new_html = str(soup)
        mail = with_replaced_content(mail, new_html)
        if not mail.get("reply-to"):
            mail["reply-to"] = mail["from"]
        mail.replace_header("from", self._get_from_addr(mail))
        mail.replace_header("subject", mail["subject"] + " (via YALB Despam)")
        mail.replace_header("message-id", self._get_message_id(mail))
        return mail

    def resend(self, mail: mailbox.Message):
        self.smtp.sendmail(mail["from"], mail["to"], mail.as_string())


def do_process_mail(args):
    with sqlite3.connect("mail.db") as sql:
        with smtplib.SMTP("localhost") as smtp:
            spool = mailbox.mbox(
                f"/var/spool/mail/{getpass.getuser()}",
                lambda msg: email.message_from_binary_file(
                    msg, policy=default_email_policy
                ),
            )
            try:
                spool.lock()
                WindsPleasure(spool, sql, smtp).run()
            finally:
                spool.close()


def with_replaced_content(email: EmailMessage, html: str) -> EmailMessage:
    new_email = cast(Any, copy.deepcopy(email))
    new_email.get_body().set_content(html, subtype="html", cte="quoted-printable")
    return new_email


def do_test(args):
    if not transforms_dir:
        raise RuntimeError("Transforms directory not specified, cannot test")
    tmpdir = Path(tempfile.mkdtemp())
    items = []
    for item in (Path(transforms_dir) / "emails").iterdir():
        if args.filter not in item.with_suffix("").name:
            continue
        if item.suffixes != [".in", ".eml"]:
            continue
        with open(item) as f:
            em = cast(
                Any,
                EmailParser(
                    policy=default_email_policy.clone(max_line_length=99999)
                ).parse(f),
            )
        old_html = em.get_body().get_payload(decode=True).decode()
        # Use html.parser instead of lxml, because, and I swear to
        # fucking god I am not making this up, some companies are
        # sending me emails that have multiple top-level <html> tags
        # and apparently this massive standards violation is silently
        # corrected by browsers while lxml correctly throws away the
        # extra document tacked at the end.
        soup = bs4.BeautifulSoup(old_html, "html.parser")
        with open(item.with_suffix("").with_suffix(".norm.eml"), "w") as f:
            f.write(str(with_replaced_content(em, str(soup))))
        with open(item.with_suffix("").with_suffix(".fmt.norm.eml"), "w") as f:
            f.write(str(with_replaced_content(em, soup.prettify())))
        for attr in dir(transforms):
            if not attr.startswith("wp_"):
                continue
            transform = getattr(transforms, attr)
            if not callable(transform):
                continue
            if new_soup := transform(soup, em):
                soup = new_soup
        new_html = str(soup)
        with open(item.with_suffix("").with_suffix(".out.eml"), "w") as f:
            f.write(str(with_replaced_content(em, str(soup))))
        with open(item.with_suffix("").with_suffix(".fmt.out.eml"), "w") as f:
            f.write(str(with_replaced_content(em, soup.prettify())))
        if args.open:
            with open(tmpdir / item.with_suffix(".html").name, "w") as f:
                f.write(old_html)
            with open(
                tmpdir / item.with_suffix("").with_suffix(".out.html").name, "w"
            ) as f:
                f.write(new_html)
            items.append(item.with_suffix("").with_suffix("").name)
    if items:
        with open(tmpdir / "viewer.html", "w") as f:
            f.write(
                """
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Wind's Pleasure Diff Viewer</title>
      </head>
      <body style="display: flex">
            """.strip()
            )
            for item in items:
                f.write(
                    f"""
        <iframe src="{item}.in.html" style="width: 40%; height: 80vh"></iframe>
        <iframe src="{item}.out.html" style="width: 40%; height: 80vh"></iframe>
                """.strip()
                )
            f.write(
                """
      </body>
    </html>

            """.strip()
            )
        webbrowser.open_new_tab(str(tmpdir / "viewer.html"))


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True)
    parser_process_mail = subparsers.add_parser("process-mail")
    parser_process_mail.set_defaults(do=do_process_mail)
    parser_test = subparsers.add_parser("test")
    parser_test.add_argument("-o", "--open", action="store_true")
    parser_test.add_argument("-f", "--filter", default="")
    parser_test.set_defaults(do=do_test)
    args = parser.parse_args()
    args.do(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
