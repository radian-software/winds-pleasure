#!/usr/bin/env python3

import argparse
from contextlib import contextmanager
import getpass
import mailbox
import smtplib
import sqlite3
import subprocess
import sys

class WindsPleasure:

    def __init__(self, spool: mailbox.Mailbox, sql: sqlite3.Connection, smtp: smtplib.SMTP):
        self.spool = spool
        self.sql = sql
        self.smtp = smtp

    def init_schema(self):
        curs = self.sql.executescript("""
          CREATE TABLE IF NOT EXISTS emails (
            original TEXT NOT NULL,
            modified TEXT NOT NULL
          );
        """)
        curs.commit()

    @contextmanager
    def recording_email(self, original: mailbox.Message, modified: mailbox.Message):
        try:
            curs = self.sql.execute("""
              INSERT INTO emails (original, modified)
              VALUES (?, ?)
            """, (original.as_string(), modified.as_string()))
            yield
            curs.commit()
        finally:
            curs.rollback()

    def run(self):
        self.init_schema()
        for key, original_mail in self.spool.iteritems():
            modified_mail = self.transform(original_mail)
            with self.recording_email(original_mail, modified_mail):
                self.resend(modified_mail)
                self.spool.remove(key)

    def transform(self, mail: mailbox.Message):
        import pdb; pdb.set_trace()
        return mail

    def resend(self, mail: mailbox.Message):
        from_address = mail["Delivered-To"]
        to_address = mail["To"]
        self.smtp.sendmail(from_address, to_address, mail.as_string())

def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    with sqlite3.connect("mail.db", autocommit=False) as sql:
        with smtplib.SMTP("localhost") as smtp:
            spool = mailbox.mbox(f"/var/spool/mail/{getpass.getuser()}")
            try:
                spool.lock()
                WindsPleasure(spool, sql, smtp).run()
            finally:
                spool.close()

if __name__ == "__main__":
    main()
    sys.exit(0)
