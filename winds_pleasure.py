#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
import email
from email.headerregistry import Address
from email.policy import default as default_email_policy
import getpass
import mailbox
import re
import smtplib
import sqlite3
import subprocess
import sys
import uuid

class WindsPleasure:

    def __init__(self, spool: mailbox.Mailbox, sql: sqlite3.Connection, smtp: smtplib.SMTP):
        self.spool = spool
        self.sql = sql
        self.smtp = smtp

    def init_schema(self):
        self.sql.executescript("""
          CREATE TABLE IF NOT EXISTS emails (
            original TEXT NOT NULL,
            modified TEXT NOT NULL
          );
        """)
        self.sql.commit()

    @contextmanager
    def recording_email(self, original: mailbox.Message, modified: mailbox.Message):
        try:
            self.sql.execute("""
              INSERT INTO emails (original, modified)
              VALUES (?, ?)
            """, (original.as_string(), modified.as_string()))
            yield
            self.sql.commit()
        finally:
            self.sql.rollback()

    def run(self):
        self.init_schema()
        for key, original_mail in self.spool.items():
            print(f"Processing mail from {original_mail['from']} with subject: {original_mail['subject']}")
            modified_mail = self.transform(original_mail)
            with self.recording_email(original_mail, modified_mail):
                self.resend(modified_mail)
                self.spool.remove(key)

    def _get_from_addr(self, mail: mailbox.Message):
        username, domain = mail["delivered-to"].split("@")
        return str(Address(
            display_name=mail["from"].addresses[0].display_name + " via YALB Despam",
            username=username,
            domain=domain,
        ))

    def _get_message_id(self, mail: mailbox.Message):
        username, orig_domain = re.sub(r"[<>]", "", mail["message-id"]).split("@")
        new_domain = mail["from"].addresses[0].domain
        return str(Address(username=username, domain=new_domain))

    def transform(self, mail: mailbox.Message):
        if not mail.get("reply-to"):
            mail["reply-to"] = mail["from"]
        mail.replace_header("from", self._get_from_addr(mail))
        mail.replace_header("subject", mail["subject"] + " (via YALB Despam)")
        mail.replace_header("message-id", self._get_message_id(mail))
        return mail

    def resend(self, mail: mailbox.Message):
        self.smtp.sendmail(mail["from"], mail["to"], mail.as_string())

def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    with sqlite3.connect("mail.db", autocommit=False) as sql:
        with smtplib.SMTP("localhost") as smtp:
            spool = mailbox.mbox(
                f"/var/spool/mail/{getpass.getuser()}",
                lambda msg: mailbox.mboxMessage(email.message_from_binary_file(msg, policy=default_email_policy)),
            )
            try:
                spool.lock()
                WindsPleasure(spool, sql, smtp).run()
            finally:
                spool.close()

if __name__ == "__main__":
    main()
    sys.exit(0)
