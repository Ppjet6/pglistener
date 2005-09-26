import sys, psycopg

import select, time, os, signal, errno, stat
from syslog import *

class PgListener:
  def __init__(self,options):
    """Creates object and make connection to server and setup a cursor for the
    connection."""

    self.options=options
    
    conn = psycopg.connect(self.options['dsn'])

    # We don't want to have to commit our transactions
    conn.autocommit(1)

    self.conn=conn
    self.cursor=conn.cursor()

    if (options.has_key("syslog") and options['syslog'].lower()=='yes'):
      # Set the appropriate syslog settings if we are using syslog
      openlog('pglistener',LOG_PID,LOG_DAEMON)

  def log(self,priority,msg):
    """Record appropriate logging information. The message that is logged
    includes the name of the configuration. The priority are those specified
    in the syslog module."""
    
    if (self.options.has_key("syslog") and self.options['syslog'].lower()=='yes'):
      # Output to syslog if syslog support is enabled
      syslog(priority,"%s: %s" %(self.options['name'],msg))
      
    print "%s: %s" % (self.options['name'], msg)

  def do_query(self):
    """Execute the query supplied returning all the rows."""
    
    self.cursor.execute(self.options['query'])
    return self.cursor.fetchall()

  def do_format(self,row):
    """Apply the format string against a row."""
    
    return self.options['format'] % row

  def do_write(self,result,target):
    """For each row in the result set apply the format and write it out to the
    target file."""
    
    f = open(target,"w+")
    for row in result:
      f.write(self.do_format(row))
    f.close

  def do_perms(self, target):
    """Apply the same file permissions from the original destination version of
    the file to the target."""
    
    if os.path.exists(self.options['destination']):
      orig = os.stat(self.options['destination'])
      try:
        os.chmod(target, orig[stat.ST_MODE])
        os.chown(target, orig[stat.ST_UID], orig[stat.ST_GID])
      except select.error, (errno, strerror):
       self.log(LOG_ERR,"Failed to chmod new file: %s" % strerror)

  def do_update(self):
    """Update the destination file with data from the database."""
    
    target = self.options['destination']+"~"
    result = self.do_query()

    self.do_write(result, target)
    self.do_perms(target)

    self.log(LOG_NOTICE,"Updating: %s" % self.options['destination'])

    os.rename(target, self.options['destination'])

  def do_posthooks(self):
    """Execute all the provided hooks."""
    
    for hook in self.options['posthooks']:
      self.log(LOG_INFO,"Executing: %s" % hook)
      os.system(hook)

  def get_notifies (self):
    """Get any pending notifications."""
    
    cursor = self.cursor

    time.sleep(0.1)
    cursor.execute("select 1")
    return cursor.notifies()

  def monitor (self):
    """Start the main monitor loop."""
    
    # Save a bit of typing ;-)
    cursor = self.cursor

    self.log(LOG_NOTICE,"Starting monitor for %s" % self.options['destination'])

    self.force_update = False
    
    # Setup a handler for SIGUSR1 which will force and update when the signal
    # is received.
    def handle_usr1(signo, frame): self.force_update = True
    signal.signal(signal.SIGUSR1, handle_usr1)

    # Setup the appropriate notifications
    for n in self.options['notifications']:
      self.log(LOG_INFO,"Listening for: %s" % n)
      cursor.execute("listen %s" % n)

    self.do_update()
    notifications = []
    while 1:
      while notifications or self.force_update:
        if self.force_update:
          self.log(LOG_NOTICE,"Got SIGUSR1, forcing update.")
          self.force_update = False
        else:
          self.log(LOG_DEBUG,"Got: %s" % notifications)
        self.do_update()
        notifications = self.get_notifies();

      self.do_posthooks()

      try:
        select.select([cursor],[],[])
      except select.error, (err, strerror):
        if err == errno.EINTR:
          pass
        else:
          raise
      else:
        notifications = self.get_notifies()

