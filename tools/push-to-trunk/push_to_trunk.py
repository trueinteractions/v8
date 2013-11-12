#!/usr/bin/env python
# Copyright 2013 the V8 project authors. All rights reserved.
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
#       copyright notice, this list of conditions and the following
#       disclaimer in the documentation and/or other materials provided
#       with the distribution.
#     * Neither the name of Google Inc. nor the names of its
#       contributors may be used to endorse or promote products derived
#       from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import datetime
import optparse
import sys
import tempfile

from common_includes import *

TRUNKBRANCH = "TRUNKBRANCH"
CHROMIUM = "CHROMIUM"
DEPS_FILE = "DEPS_FILE"

CONFIG = {
  BRANCHNAME: "prepare-push",
  TRUNKBRANCH: "trunk-push",
  PERSISTFILE_BASENAME: "/tmp/v8-push-to-trunk-tempfile",
  TEMP_BRANCH: "prepare-push-temporary-branch-created-by-script",
  DOT_GIT_LOCATION: ".git",
  VERSION_FILE: "src/version.cc",
  CHANGELOG_FILE: "ChangeLog",
  CHANGELOG_ENTRY_FILE: "/tmp/v8-push-to-trunk-tempfile-changelog-entry",
  PATCH_FILE: "/tmp/v8-push-to-trunk-tempfile-patch-file",
  COMMITMSG_FILE: "/tmp/v8-push-to-trunk-tempfile-commitmsg",
  DEPS_FILE: "DEPS",
}


class Preparation(Step):
  def __init__(self):
    Step.__init__(self, "Preparation.")

  def RunStep(self):
    self.InitialEnvironmentChecks()
    self.CommonPrepare()
    self.DeleteBranch(self.Config(TRUNKBRANCH))


class FreshBranch(Step):
  def __init__(self):
    Step.__init__(self, "Create a fresh branch.")

  def RunStep(self):
    args = "checkout -b %s svn/bleeding_edge" % self.Config(BRANCHNAME)
    if self.Git(args) is None:
      self.Die("Creating branch %s failed." % self.Config(BRANCHNAME))


class DetectLastPush(Step):
  def __init__(self):
    Step.__init__(self, "Detect commit ID of last push to trunk.")

  def RunStep(self):
    last_push = (self._options.l or
                 self.Git("log -1 --format=%H ChangeLog").strip())
    while True:
      # Print assumed commit, circumventing git's pager.
      print self.Git("log -1 %s" % last_push)
      if self.Confirm("Is the commit printed above the last push to trunk?"):
        break
      args = "log -1 --format=%H %s^ ChangeLog" % last_push
      last_push = self.Git(args).strip()
    self.Persist("last_push", last_push)
    self._state["last_push"] = last_push


class PrepareChangeLog(Step):
  def __init__(self):
    Step.__init__(self, "Prepare raw ChangeLog entry.")

  def RunStep(self):
    self.RestoreIfUnset("last_push")

    # These version numbers are used again later for the trunk commit.
    self.ReadAndPersistVersion()

    date = datetime.date.today().strftime("%Y-%m-%d")
    self.Persist("date", date)
    output = "%s: Version %s.%s.%s\n\n" % (date,
                                           self._state["major"],
                                           self._state["minor"],
                                           self._state["build"])
    TextToFile(output, self.Config(CHANGELOG_ENTRY_FILE))

    args = "log %s..HEAD --format=%%H" % self._state["last_push"]
    commits = self.Git(args).strip()
    for commit in commits.splitlines():
      # Get the commit's title line.
      args = "log -1 %s --format=\"%%w(80,8,8)%%s\"" % commit
      title = "%s\n" % self.Git(args).rstrip()
      AppendToFile(title, self.Config(CHANGELOG_ENTRY_FILE))

      # Grep for "BUG=xxxx" lines in the commit message and convert them to
      # "(issue xxxx)".
      out = self.Git("log -1 %s --format=\"%%B\"" % commit).splitlines()
      out = filter(lambda x: re.search(r"^BUG=", x), out)
      out = filter(lambda x: not re.search(r"BUG=$", x), out)
      out = filter(lambda x: not re.search(r"BUG=none$", x), out)

      # TODO(machenbach): Handle multiple entries (e.g. BUG=123, 234).
      def FormatIssue(text):
        text = re.sub(r"BUG=v8:(.*)$", r"(issue \1)", text)
        text = re.sub(r"BUG=chromium:(.*)$", r"(Chromium issue \1)", text)
        text = re.sub(r"BUG=(.*)$", r"(Chromium issue \1)", text)
        return "        %s\n" % text

      for line in map(FormatIssue, out):
        AppendToFile(line, self.Config(CHANGELOG_ENTRY_FILE))

      # Append the commit's author for reference.
      args = "log -1 %s --format=\"%%w(80,8,8)(%%an)\"" % commit
      author = self.Git(args).rstrip()
      AppendToFile("%s\n\n" % author, self.Config(CHANGELOG_ENTRY_FILE))

    msg = "        Performance and stability improvements on all platforms.\n"
    AppendToFile(msg, self.Config(CHANGELOG_ENTRY_FILE))

class EditChangeLog(Step):
  def __init__(self):
    Step.__init__(self, "Edit ChangeLog entry.")

  def RunStep(self):
    print ("Please press <Return> to have your EDITOR open the ChangeLog "
           "entry, then edit its contents to your liking. When you're done, "
           "save the file and exit your EDITOR. ")
    self.ReadLine()

    self.Editor(self.Config(CHANGELOG_ENTRY_FILE))
    handle, new_changelog = tempfile.mkstemp()
    os.close(handle)

    # (1) Eliminate tabs, (2) fix too little and (3) too much indentation, and
    # (4) eliminate trailing whitespace.
    changelog_entry = FileToText(self.Config(CHANGELOG_ENTRY_FILE)).rstrip()
    changelog_entry = MSub(r"\t", r"        ", changelog_entry)
    changelog_entry = MSub(r"^ {1,7}([^ ])", r"        \1", changelog_entry)
    changelog_entry = MSub(r"^ {9,80}([^ ])", r"        \1", changelog_entry)
    changelog_entry = MSub(r" +$", r"", changelog_entry)

    if changelog_entry == "":
      self.Die("Empty ChangeLog entry.")

    with open(new_changelog, "w") as f:
      f.write(changelog_entry)
      f.write("\n\n\n")  # Explicitly insert two empty lines.

    AppendToFile(FileToText(self.Config(CHANGELOG_FILE)), new_changelog)
    TextToFile(FileToText(new_changelog), self.Config(CHANGELOG_FILE))
    os.remove(new_changelog)


class IncrementVersion(Step):
  def __init__(self):
    Step.__init__(self, "Increment version number.")

  def RunStep(self):
    self.RestoreIfUnset("build")
    new_build = str(int(self._state["build"]) + 1)

    if self.Confirm(("Automatically increment BUILD_NUMBER? (Saying 'n' will "
                     "fire up your EDITOR on %s so you can make arbitrary "
                     "changes. When you're done, save the file and exit your "
                     "EDITOR.)" % self.Config(VERSION_FILE))):
      text = FileToText(self.Config(VERSION_FILE))
      text = MSub(r"(?<=#define BUILD_NUMBER)(?P<space>\s+)\d*$",
                  r"\g<space>%s" % new_build,
                  text)
      TextToFile(text, self.Config(VERSION_FILE))
    else:
      self.Editor(self.Config(VERSION_FILE))

    self.ReadAndPersistVersion("new_")


class CommitLocal(Step):
  def __init__(self):
    Step.__init__(self, "Commit to local branch.")

  def RunStep(self):
    self.RestoreVersionIfUnset("new_")
    prep_commit_msg = ("Prepare push to trunk.  "
        "Now working on version %s.%s.%s." % (self._state["new_major"],
                                              self._state["new_minor"],
                                              self._state["new_build"]))
    self.Persist("prep_commit_msg", prep_commit_msg)
    if self.Git("commit -a -m \"%s\"" % prep_commit_msg) is None:
      self.Die("'git commit -a' failed.")


class CommitRepository(Step):
  def __init__(self):
    Step.__init__(self, "Commit to the repository.")

  def RunStep(self):
    self.WaitForLGTM()
    # Re-read the ChangeLog entry (to pick up possible changes).
    # FIXME(machenbach): This was hanging once with a broken pipe.
    TextToFile(Command("cat %s | awk --posix '{\
        if ($0 ~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}:/) {\
          if (in_firstblock == 1) {\
            exit 0;\
          } else {\
            in_firstblock = 1;\
          }\
        };\
        print $0;\
      }'" % self.Config(CHANGELOG_FILE)), self.Config(CHANGELOG_ENTRY_FILE))

    if self.Git("cl dcommit", "PRESUBMIT_TREE_CHECK=\"skip\"") is None:
      self.Die("'git cl dcommit' failed, please try again.")


class StragglerCommits(Step):
  def __init__(self):
    Step.__init__(self, "Fetch straggler commits that sneaked in since this "
                        "script was started.")

  def RunStep(self):
    if self.Git("svn fetch") is None:
      self.Die("'git svn fetch' failed.")
    self.Git("checkout svn/bleeding_edge")
    self.RestoreIfUnset("prep_commit_msg")
    args = "log -1 --format=%%H --grep=\"%s\"" % self._state["prep_commit_msg"]
    prepare_commit_hash = self.Git(args).strip()
    self.Persist("prepare_commit_hash", prepare_commit_hash)


class SquashCommits(Step):
  def __init__(self):
    Step.__init__(self, "Squash commits into one.")

  def RunStep(self):
    # Instead of relying on "git rebase -i", we'll just create a diff, because
    # that's easier to automate.
    self.RestoreIfUnset("prepare_commit_hash")
    args = "diff svn/trunk %s" % self._state["prepare_commit_hash"]
    TextToFile(self.Git(args), self.Config(PATCH_FILE))

    # Convert the ChangeLog entry to commit message format:
    # - remove date
    # - remove indentation
    # - merge paragraphs into single long lines, keeping empty lines between
    #   them.
    self.RestoreIfUnset("date")
    changelog_entry = FileToText(self.Config(CHANGELOG_ENTRY_FILE))

    # TODO(machenbach): This could create a problem if the changelog contained
    # any quotation marks.
    text = Command("echo \"%s\" \
        | sed -e \"s/^%s: //\" \
        | sed -e 's/^ *//' \
        | awk '{ \
            if (need_space == 1) {\
              printf(\" \");\
            };\
            printf(\"%%s\", $0);\
            if ($0 ~ /^$/) {\
              printf(\"\\n\\n\");\
              need_space = 0;\
            } else {\
              need_space = 1;\
            }\
          }'" % (changelog_entry, self._state["date"]))

    if not text:
      self.Die("Commit message editing failed.")
    TextToFile(text, self.Config(COMMITMSG_FILE))
    os.remove(self.Config(CHANGELOG_ENTRY_FILE))


class NewBranch(Step):
  def __init__(self):
    Step.__init__(self, "Create a new branch from trunk.")

  def RunStep(self):
    if self.Git("checkout -b %s svn/trunk" % self.Config(TRUNKBRANCH)) is None:
      self.Die("Checking out a new branch '%s' failed." %
               self.Config(TRUNKBRANCH))


class ApplyChanges(Step):
  def __init__(self):
    Step.__init__(self, "Apply squashed changes.")

  def RunStep(self):
    self.ApplyPatch(self.Config(PATCH_FILE))
    Command("rm", "-f %s*" % self.Config(PATCH_FILE))


class SetVersion(Step):
  def __init__(self):
    Step.__init__(self, "Set correct version for trunk.")

  def RunStep(self):
    self.RestoreVersionIfUnset()
    output = ""
    for line in FileToText(self.Config(VERSION_FILE)).splitlines():
      if line.startswith("#define MAJOR_VERSION"):
        line = re.sub("\d+$", self._state["major"], line)
      elif line.startswith("#define MINOR_VERSION"):
        line = re.sub("\d+$", self._state["minor"], line)
      elif line.startswith("#define BUILD_NUMBER"):
        line = re.sub("\d+$", self._state["build"], line)
      elif line.startswith("#define PATCH_LEVEL"):
        line = re.sub("\d+$", "0", line)
      elif line.startswith("#define IS_CANDIDATE_VERSION"):
        line = re.sub("\d+$", "0", line)
      output += "%s\n" % line
    TextToFile(output, self.Config(VERSION_FILE))


class CommitTrunk(Step):
  def __init__(self):
    Step.__init__(self, "Commit to local trunk branch.")

  def RunStep(self):
    self.Git("add \"%s\"" % self.Config(VERSION_FILE))
    if self.Git("commit -F \"%s\"" % self.Config(COMMITMSG_FILE)) is None:
      self.Die("'git commit' failed.")
    Command("rm", "-f %s*" % self.Config(COMMITMSG_FILE))


class SanityCheck(Step):
  def __init__(self):
    Step.__init__(self, "Sanity check.")

  def RunStep(self):
    if not self.Confirm("Please check if your local checkout is sane: Inspect "
        "%s, compile, run tests. Do you want to commit this new trunk "
        "revision to the repository?" % self.Config(VERSION_FILE)):
      self.Die("Execution canceled.")


class CommitSVN(Step):
  def __init__(self):
    Step.__init__(self, "Commit to SVN.")

  def RunStep(self):
    result = self.Git("svn dcommit 2>&1")
    if not result:
      self.Die("'git svn dcommit' failed.")
    result = filter(lambda x: re.search(r"^Committed r[0-9]+", x),
                    result.splitlines())
    if len(result) > 0:
      trunk_revision = re.sub(r"^Committed r([0-9]+)", r"\1", result[0])

    # Sometimes grepping for the revision fails. No idea why. If you figure
    # out why it is flaky, please do fix it properly.
    if not trunk_revision:
      print("Sorry, grepping for the SVN revision failed. Please look for it "
            "in the last command's output above and provide it manually (just "
            "the number, without the leading \"r\").")
      while not trunk_revision:
        print "> ",
        trunk_revision = self.ReadLine()
    self.Persist("trunk_revision", trunk_revision)


class TagRevision(Step):
  def __init__(self):
    Step.__init__(self, "Tag the new revision.")

  def RunStep(self):
    self.RestoreVersionIfUnset()
    ver = "%s.%s.%s" % (self._state["major"],
                        self._state["minor"],
                        self._state["build"])
    if self.Git("svn tag %s -m \"Tagging version %s\"" % (ver, ver)) is None:
      self.Die("'git svn tag' failed.")


class CheckChromium(Step):
  def __init__(self):
    Step.__init__(self, "Ask for chromium checkout.")

  def Run(self):
    chrome_path = self._options.c
    if not chrome_path:
      print ("Do you have a \"NewGit\" Chromium checkout and want "
          "this script to automate creation of the roll CL? If yes, enter the "
          "path to (and including) the \"src\" directory here, otherwise just "
          "press <Return>: "),
      chrome_path = self.ReadLine()
    self.Persist("chrome_path", chrome_path)


class SwitchChromium(Step):
  def __init__(self):
    Step.__init__(self, "Switch to Chromium checkout.", requires="chrome_path")

  def RunStep(self):
    v8_path = os.getcwd()
    self.Persist("v8_path", v8_path)
    os.chdir(self._state["chrome_path"])
    self.InitialEnvironmentChecks()
    # Check for a clean workdir.
    if self.Git("status -s -uno").strip() != "":
      self.Die("Workspace is not clean. Please commit or undo your changes.")
    # Assert that the DEPS file is there.
    if not os.path.exists(self.Config(DEPS_FILE)):
      self.Die("DEPS file not present.")


class UpdateChromiumCheckout(Step):
  def __init__(self):
    Step.__init__(self, "Update the checkout and create a new branch.",
                  requires="chrome_path")

  def RunStep(self):
    os.chdir(self._state["chrome_path"])
    if self.Git("checkout master") is None:
      self.Die("'git checkout master' failed.")
    if self.Git("pull") is None:
      self.Die("'git pull' failed, please try again.")

    self.RestoreIfUnset("trunk_revision")
    args = "checkout -b v8-roll-%s" % self._state["trunk_revision"]
    if self.Git(args) is None:
      self.Die("Failed to checkout a new branch.")


class UploadCL(Step):
  def __init__(self):
    Step.__init__(self, "Create and upload CL.", requires="chrome_path")

  def RunStep(self):
    os.chdir(self._state["chrome_path"])

    # Patch DEPS file.
    self.RestoreIfUnset("trunk_revision")
    deps = FileToText(self.Config(DEPS_FILE))
    deps = re.sub("(?<=\"v8_revision\": \")([0-9]+)(?=\")",
                  self._state["trunk_revision"],
                  deps)
    TextToFile(deps, self.Config(DEPS_FILE))

    self.RestoreVersionIfUnset()
    ver = "%s.%s.%s" % (self._state["major"],
                        self._state["minor"],
                        self._state["build"])
    print "Please enter the email address of a reviewer for the roll CL: ",
    rev = self.ReadLine()
    args = "commit -am \"Update V8 to version %s.\n\nTBR=%s\"" % (ver, rev)
    if self.Git(args) is None:
      self.Die("'git commit' failed.")
    if self.Git("cl upload --send-mail", pipe=False) is None:
      self.Die("'git cl upload' failed, please try again.")
    print "CL uploaded."


class SwitchV8(Step):
  def __init__(self):
    Step.__init__(self, "Returning to V8 checkout.", requires="chrome_path")

  def RunStep(self):
    self.RestoreIfUnset("v8_path")
    os.chdir(self._state["v8_path"])


class CleanUp(Step):
  def __init__(self):
    Step.__init__(self, "Done!")

  def RunStep(self):
    self.RestoreVersionIfUnset()
    ver = "%s.%s.%s" % (self._state["major"],
                        self._state["minor"],
                        self._state["build"])
    self.RestoreIfUnset("trunk_revision")
    self.RestoreIfUnset("chrome_path")

    if self._state["chrome_path"]:
      print("Congratulations, you have successfully created the trunk "
            "revision %s and rolled it into Chromium. Please don't forget to "
            "update the v8rel spreadsheet:" % ver)
    else:
      print("Congratulations, you have successfully created the trunk "
            "revision %s. Please don't forget to roll this new version into "
            "Chromium, and to update the v8rel spreadsheet:" % ver)
    print "%s\ttrunk\t%s" % (ver, self._state["trunk_revision"])

    self.CommonCleanup()
    if self.Config(TRUNKBRANCH) != self._state["current_branch"]:
      self.Git("branch -D %s" % self.Config(TRUNKBRANCH))


def RunScript(config,
              options,
              side_effect_handler=DEFAULT_SIDE_EFFECT_HANDLER):
  step_classes = [
    Preparation,
    FreshBranch,
    DetectLastPush,
    PrepareChangeLog,
    EditChangeLog,
    IncrementVersion,
    CommitLocal,
    UploadStep,
    CommitRepository,
    StragglerCommits,
    SquashCommits,
    NewBranch,
    ApplyChanges,
    SetVersion,
    CommitTrunk,
    SanityCheck,
    CommitSVN,
    TagRevision,
    CheckChromium,
    SwitchChromium,
    UpdateChromiumCheckout,
    UploadCL,
    SwitchV8,
    CleanUp,
  ]

  state = {}
  steps = []
  number = 0

  for step_class in step_classes:
    # TODO(machenbach): Factory methods.
    step = step_class()
    step.SetNumber(number)
    step.SetConfig(config)
    step.SetOptions(options)
    step.SetState(state)
    step.SetSideEffectHandler(side_effect_handler)
    steps.append(step)
    number += 1

  for step in steps[options.s:]:
    step.Run()


def BuildOptions():
  result = optparse.OptionParser()
  result.add_option("-s", "--step", dest="s",
                    help="Specify the step where to start work. Default: 0.",
                    default=0, type="int")
  result.add_option("-l", "--last-push", dest="l",
                    help=("Manually specify the git commit ID "
                          "of the last push to trunk."))
  result.add_option("-c", "--chromium", dest="c",
                    help=("Specify the path to your Chromium src/ "
                          "directory to automate the V8 roll."))
  return result


def ProcessOptions(options):
  if options.s < 0:
    print "Bad step number %d" % options.s
    return False
  return True


def Main():
  parser = BuildOptions()
  (options, args) = parser.parse_args()
  if not ProcessOptions(options):
    parser.print_help()
    return 1
  RunScript(CONFIG, options)

if __name__ == "__main__":
  sys.exit(Main())
