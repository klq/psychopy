import sys, time, types, re
import wx, wx.stc, wx.aui, wx.richtext
import keyword, os, sys, string, StringIO, glob
import threading, traceback, bdb, cPickle
import psychoParser
import introspect, py_compile

if wx.Platform == '__WXMSW__':
    faces = { 'times': 'Times New Roman',
              'mono' : 'Courier New',
              'helv' : 'Arial',
              'other': 'Comic Sans MS',
              'size' : 10,
              'size2': 8,
             }
elif wx.Platform == '__WXMAC__':
    faces = { 'times': 'Times New Roman',
              'mono' : 'Courier New',
              'helv' : 'Arial',
              'other': 'Comic Sans MS',
              'size' : 14,
              'size2': 12,
             }
else:
    faces = { 'times': 'Times',
              'mono' : 'Courier',
              'helv' : 'Helvetica',
              'other': 'new century schoolbook',
              'size' : 12,
              'size2': 10,
             }           

def toPickle(filename, data):
    """save data (of any sort) as a pickle file
    
    simple wrapper of the cPickle module in core python
    """
    f = open(filename, 'w')
    cPickle.dump(data,f)
    f.close()

def fromPickle(filename):
    """load data (of any sort) from a pickle file
    
    simple wrapper of the cPickle module in core python
    """
    f = open(filename)
    contents = cPickle.load(f)
    f.close()
    return contents

class ScriptThread(threading.Thread):
    """A subclass of threading.Thread, with a kill()
    method."""
    def __init__(self, target, gui):
        threading.Thread.__init__(self, target=target)
        self.killed = False
        self.gui=gui

    def start(self):
        """Start the thread."""
        self.__run_backup = self.run
        self.run = self.__run      # Force the Thread toinstall our trace.
        threading.Thread.start(self)

    def __run(self):
        """Hacked run function, which installs the
        trace."""
        sys.settrace(self.globaltrace)
        self.__run_backup()
        self.run = self.__run_backup        
        #we're done - send the App a message
        self.gui.onProcessEnded(event=None)
  
    def globaltrace(self, frame, why, arg):
        if why == 'call':
            return self.localtrace
        else:
            return None
  
    def localtrace(self, frame, why, arg):
        if self.killed:
            if why == 'line':
                raise SystemExit()
        return self.localtrace
    
    def kill(self):
        self.killed = True
        
class PsychoDebugger(bdb.Bdb):
    #this is based on effbot:
    #http://effbot.org/librarybook/bdb.htm
    def __init__(self):
        bdb.Bdb.__init__(self)
        self.starting = True
    def user_call(self, frame, args):
        name = frame.f_code.co_name or "<unknown>"
        #print "call", name, args
        self.set_continue() # continue
    
    def user_line(self, frame):
        if self.starting:
            self.starting = False
            self.set_trace() # start tracing
        else:
            # arrived at breakpoint
            name = frame.f_code.co_name or "<unknown>"
            filename = self.canonic(frame.f_code.co_filename)
            print "break at", filename, frame.f_lineno, "in", name
        self.set_continue() # continue to next breakpoint

    def user_return(self, frame, value):
        name = frame.f_code.co_name or "<unknown>"
        print "return from", name, value
        print "returnCont..."
        self.set_continue() # continue

    def user_exception(self, frame, exception):
        name = frame.f_code.co_name or "<unknown>"
        print "exception in", name, exception
        print "excCont..."
        self.set_continue() # continue
    def quit(self):
        self._user_requested_quit = 1
        self.set_quit()
        return 1
    
class ModuleLoader(threading.Thread):
    #a threading class to run the scripts
    def __init__(self, parent):
        self.parent=parent#parent should be the main frame
        assert isinstance(self.parent, IDEMainFrame)
        self.complete=False
        self.run()
    def run(self):    
        self.parent.SetStatusText('importing modules')
        import psychopy  
        self.parent.SetStatusText('importing numpy')
        import numpy
        self.parent.SetStatusText('importing scipy')
        import scipy
        self.parent.SetStatusText('importing pylab')
        import pylab
        import monitors
        self.complete=True
        self.parent.modulesLoaded=True
        self.parent.analyseCodeNow(event=None)
 
class CodeEditor(wx.stc.StyledTextCtrl):
    # this comes mostly from the wxPython demo styledTextCtrl 2
    def __init__(self, parent, ID, frame,
                 pos=wx.DefaultPosition, size=wx.Size(100,100),#set the viewer to be small, then it will increase with wx.aui control
                 style=0):
        wx.stc.StyledTextCtrl.__init__(self, parent, ID, pos, size, style)
        #JWP additions
        self.parent=parent
        self.frame = frame
        self.UNSAVED=False
        self.filename=""
        self.AUTOCOMPLETE = True
        self.autoCompleteDict={}
        #self.analyseScript()  #no - analyse after loading so that window doesn't pause strangely
        self.locals = None #this will contain the local environment of the script
        self.prevWord=None
        #remove some annoying stc key commands
        self.CmdKeyClear(ord('['), wx.stc.STC_SCMOD_CTRL)
        self.CmdKeyClear(ord(']'), wx.stc.STC_SCMOD_CTRL)
        self.CmdKeyClear(ord('/'), wx.stc.STC_SCMOD_CTRL)
        self.CmdKeyClear(ord('/'), wx.stc.STC_SCMOD_CTRL|wx.stc.STC_SCMOD_SHIFT)
        
        self.SetLexer(wx.stc.STC_LEX_PYTHON)
        self.SetKeyWords(0, " ".join(keyword.kwlist))

        self.SetProperty("fold", "1")
        self.SetProperty("tab.timmy.whinge.level", "1")
        self.SetMargins(0,0)
        self.SetUseTabs(False)
        self.SetTabWidth(4)
        self.SetViewWhiteSpace(False)
        #self.SetBufferedDraw(False)
        self.SetViewEOL(False)
        self.SetEOLMode(wx.stc.STC_EOL_LF)
        self.SetUseAntiAliasing(True)
        #self.SetUseHorizontalScrollBar(True)
        #self.SetUseVerticalScrollBar(True)
        
        #self.SetEdgeMode(wx.stc.STC_EDGE_BACKGROUND)
        #self.SetEdgeMode(wx.stc.STC_EDGE_LINE)
        #self.SetEdgeColumn(78)
        
        # Setup a margin to hold fold markers
        self.SetMarginType(2, wx.stc.STC_MARGIN_SYMBOL)
        self.SetMarginMask(2, wx.stc.STC_MASK_FOLDERS)
        self.SetMarginSensitive(2, True)
        self.SetMarginWidth(2, 12)

        # Like a flattened tree control using square headers
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEROPEN,    wx.stc.STC_MARK_BOXMINUS,          "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDER,        wx.stc.STC_MARK_BOXPLUS,           "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERSUB,     wx.stc.STC_MARK_VLINE,             "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERTAIL,    wx.stc.STC_MARK_LCORNER,           "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEREND,     wx.stc.STC_MARK_BOXPLUSCONNECTED,  "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDEROPENMID, wx.stc.STC_MARK_BOXMINUSCONNECTED, "white", "#808080")
        self.MarkerDefine(wx.stc.STC_MARKNUM_FOLDERMIDTAIL, wx.stc.STC_MARK_TCORNER,           "white", "#808080")
        
        self.Bind(wx.stc.EVT_STC_MODIFIED, self.onModified)
        #self.Bind(wx.stc.EVT_STC_UPDATEUI, self.OnUpdateUI)
        self.Bind(wx.stc.EVT_STC_MARGINCLICK, self.OnMarginClick)
        self.Bind(wx.EVT_KEY_DOWN, self.OnKeyPressed)
        
        # Make some styles,  The lexer defines what each style is used for, we
        # just have to define what each style looks like.  This set is adapted from
        # Scintilla sample property files.
        
        # Global default styles for all languages
        self.StyleSetSpec(wx.stc.STC_STYLE_DEFAULT,     "face:%(helv)s,size:%(size)d" % faces)
        self.StyleClearAll()  # Reset all to be like the default

        # Global default styles for all languages
        self.StyleSetSpec(wx.stc.STC_STYLE_DEFAULT,     "face:%(helv)s,size:%(size)d" % faces)
        self.StyleSetSpec(wx.stc.STC_STYLE_LINENUMBER,  "back:#C0C0C0,face:%(helv)s,size:%(size2)d" % faces)
        self.StyleSetSpec(wx.stc.STC_STYLE_CONTROLCHAR, "face:%(other)s" % faces)
        self.StyleSetSpec(wx.stc.STC_STYLE_BRACELIGHT,  "fore:#FFFFFF,back:#0000FF,bold")
        self.StyleSetSpec(wx.stc.STC_STYLE_BRACEBAD,    "fore:#000000,back:#FF0000,bold")

        # Python styles
        # Default 
        self.StyleSetSpec(wx.stc.STC_P_DEFAULT, "fore:#000000,face:%(helv)s,size:%(size)d" % faces)
        # Comments
        self.StyleSetSpec(wx.stc.STC_P_COMMENTLINE, "fore:#007F00,face:%(other)s,size:%(size)d" % faces)
        # Number
        self.StyleSetSpec(wx.stc.STC_P_NUMBER, "fore:#007F7F,size:%(size)d" % faces)
        # String
        self.StyleSetSpec(wx.stc.STC_P_STRING, "fore:#7F007F,face:%(helv)s,size:%(size)d" % faces)
        # Single quoted string
        self.StyleSetSpec(wx.stc.STC_P_CHARACTER, "fore:#7F007F,face:%(helv)s,size:%(size)d" % faces)
        # Keyword
        self.StyleSetSpec(wx.stc.STC_P_WORD, "fore:#00007F,bold,size:%(size)d" % faces)
        # Triple quotes
        self.StyleSetSpec(wx.stc.STC_P_TRIPLE, "fore:#7F0000,size:%(size)d" % faces)
        # Triple double quotes
        self.StyleSetSpec(wx.stc.STC_P_TRIPLEDOUBLE, "fore:#7F0000,size:%(size)d" % faces)
        # Class name definition
        self.StyleSetSpec(wx.stc.STC_P_CLASSNAME, "fore:#0000FF,bold,underline,size:%(size)d" % faces)
        # Function or method name definition
        self.StyleSetSpec(wx.stc.STC_P_DEFNAME, "fore:#007F7F,bold,size:%(size)d" % faces)
        # Operators
        self.StyleSetSpec(wx.stc.STC_P_OPERATOR, "bold,size:%(size)d" % faces)
        # Identifiers
        self.StyleSetSpec(wx.stc.STC_P_IDENTIFIER, "fore:#000000,face:%(helv)s,size:%(size)d" % faces)
        # Comment-blocks
        self.StyleSetSpec(wx.stc.STC_P_COMMENTBLOCK, "fore:#7F7F7F,size:%(size)d" % faces)
        # End of line where string is not closed
        self.StyleSetSpec(wx.stc.STC_P_STRINGEOL, "fore:#000000,face:%(helv)s,back:#E0C0E0,eol,size:%(size)d" % faces)

        self.SetCaretForeground("BLUE")
        
    def OnKeyPressed(self, event):
        #various stuff to handle code completion and tooltips
        #enable in the _-init__
        if self.CallTipActive():
            self.CallTipCancel()
        keyCode = event.GetKeyCode()
        
        #handle some special keys
        if keyCode== ord('[') and (wx.MOD_CONTROL == event.GetModifiers()):
            self.indentSelection(-4)
            #if there are no characters on the line then also move caret to end of indentation
            txt, charPos = self.GetCurLine()
            if charPos==0: self.VCHome()#if caret is at start of line then move to start of text instead
        if keyCode== ord(']') and (wx.MOD_CONTROL == event.GetModifiers()):
            self.indentSelection(4)
            #if there are no characters on the line then also move caret to end of indentation
            txt, charPos = self.GetCurLine()
            if charPos==0: self.VCHome()#if caret is at start of line then move to start of text instead
                
        if keyCode== ord('/') and (wx.MOD_CONTROL == event.GetModifiers()):
            self.commentLines()
        if keyCode== ord('/') and (wx.MOD_CONTROL|wx.MOD_SHIFT == event.GetModifiers()):
            self.uncommentLines()
        
        #do code completion
        if self.AUTOCOMPLETE:
            #get last word any previous word (if there was a dot instead of space)
            isAlphaNum = (keyCode in range(65,91) or keyCode in range(97,123))
            isDot = (keyCode==46)
            prevWord = None
            if isAlphaNum:#any alphanum
                #is character key
                key = chr(keyCode)
                #if keyCode == 32 and event.ControlDown(): #Ctrl-space
                pos = self.GetCurrentPos()
                prevStartPos = startPos = self.WordStartPosition(pos, True)
                currWord = self.GetTextRange(startPos, pos)+key
            
                #check if this is an attribute of another class etc...
                while self.GetCharAt(prevStartPos-1)==46:#then previous char was .
                    prevStartPos = self.WordStartPosition(prevStartPos-1, True)
                    prevWord = self.GetTextRange(prevStartPos, startPos-1)
            
            #slightly different if this char is itself a dot
            elif isDot: #we have a '.' so look for methods/attributes
                pos = self.GetCurrentPos()
                prevStartPos=startPos = self.WordStartPosition(pos, True)
                prevWord = self.GetTextRange(startPos, pos)
                currWord=''
                while self.GetCharAt(prevStartPos-1)==46:#then previous char was .
                    prevStartPos = self.WordStartPosition(prevStartPos-1, True)
                    prevWord = self.GetTextRange(prevStartPos, pos-1)
                
            self.AutoCompSetIgnoreCase(True)
            self.AutoCompSetAutoHide(True)
            #try to get attributes for this object
            event.Skip()
            if isAlphaNum or isDot:
                
                if True:#use our own dictionary
                    #after a '.' show attributes
                    subList=[]#by default
                    if prevWord: #did we get a word?
                        if prevWord in self.autoCompleteDict.keys(): #is it in dictionary?
                            attrs = self.autoCompleteDict[prevWord]['attrs']
                            if type(attrs)==list and len(attrs)>=1: #does it have known attributes?
                                subList = [ s for s in attrs if string.find(s.lower(), currWord.lower()) != -1 ]    
                    #for objects show simple completions              
                    else:#there was no preceding '.'                    
                        if len(currWord)>1 and len(self.autoCompleteDict.keys())>1: #start trying after 2 characters
                            subList = [ s for s in self.autoCompleteDict.keys() if string.find(s.lower(), currWord.lower()) != -1 ]
                else:#use introspect (from wxpython's py package)
                    pass#
                #if there were any reasonable matches then show them
                if len(subList)>0:
                    subList.sort()
                    self.AutoCompShow(len(currWord)-1, " ".join(subList))
        
        if keyCode == wx.WXK_RETURN and not self.AutoCompActive():
            #prcoess end of line and then do smart indentation
            event.Skip(False)
            self.CmdKeyExecute(wx.stc.STC_CMD_NEWLINE)
            self.smartIdentThisLine()
            return #so that we don't reach the skip line at end        
        
        event.Skip()
    def smartIdentThisLine(self):
        startLineNum = self.LineFromPosition(self.GetSelectionStart())
        endLineNum = self.LineFromPosition(self.GetSelectionEnd())
        prevLine = self.GetLine(startLineNum-1)
        prevIndent = self.GetLineIndentation(startLineNum-1)
        
        #set the indent
        self.SetLineIndentation(startLineNum, prevIndent)
        #self.LineEnd() #move cursor to end of line - is good if user is starting a new line but not if they hit shift-tab
        #self.SetPosition(startLineNum+prevIndent)#move the cursor to the end of the indented section
        self.VCHome()
        
        #check for a colon to signal an indent decrease
        prevLogical = string.split(prevLine, '#')[0]
        prevLogical = string.strip(prevLogical)
        if len(prevLogical)>0 and prevLogical[-1]== ':':
            self.CmdKeyExecute(wx.stc.STC_CMD_TAB)
            
    def smartIndent(self):
        #find out about current positions and indentation
        startLineNum = self.LineFromPosition(self.GetSelectionStart())
        endLineNum = self.LineFromPosition(self.GetSelectionEnd())
        prevLine = self.GetLine(startLineNum-1)
        prevIndent = self.GetLineIndentation(startLineNum-1)
        startLineIndent = self.GetLineIndentation(startLineNum)
        
        #calculate how much we need to increment/decrement the current lines
        incr = prevIndent-startLineIndent
        #check for a colon to signal an indent decrease
        prevLogical = string.split(prevLine, '#')[0]
        prevLogical = string.strip(prevLogical)
        if len(prevLogical)>0 and prevLogical[-1]== ':':
            incr = incr+4
            
        #set each line to the correct indentation
        for lineNum in range(startLineNum, endLineNum+1):
            thisIndent = self.GetLineIndentation(lineNum)
            self.SetLineIndentation(lineNum, thisIndent+incr)
    def shouldTrySmartIndent(self):
        #used when the user presses tab key to decide whether to insert a tab char
        #or whether to smart indent text
        
        #if some text has been selected then use indentation    
        if len(self.GetSelectedText())>0:
            return True       
        
        #test whether any text precedes current pos
        lineText, posOnLine = self.GetCurLine()
        textBeforeCaret = lineText[:posOnLine]
        if textBeforeCaret.split()==[]:
            return True
        else:
            return False
        
    def indentSelection(self, howFar=4):
        #Indent or outdent current selection by 'howFar' spaces 
        #(which could be positive or negative int).
        startLineNum = self.LineFromPosition(self.GetSelectionStart())
        endLineNum = self.LineFromPosition(self.GetSelectionEnd())
        #go through line-by-line
        for lineN in range(startLineNum, endLineNum+1):
            newIndent = self.GetLineIndentation(lineN) + howFar
            if newIndent<0:newIndent=0
            self.SetLineIndentation(lineN, newIndent)
        
    
    def OnUpdateUI(self, evt):
        # check for matching braces
        braceAtCaret = -1
        braceOpposite = -1
        charBefore = None
        caretPos = self.GetCurrentPos()

        if caretPos > 0:
            charBefore = self.GetCharAt(caretPos - 1)
            styleBefore = self.GetStyleAt(caretPos - 1)

        # check before
        if charBefore and chr(charBefore) in "[]{}()" and styleBefore == wx.stc.STC_P_OPERATOR:

            braceAtCaret = caretPos - 1

        # check after
        if braceAtCaret < 0:
            charAfter = self.GetCharAt(caretPos)
            styleAfter = self.GetStyleAt(caretPos)

            if charAfter and chr(charAfter) in "[]{}()" and styleAfter == wx.stc.STC_P_OPERATOR:
                braceAtCaret = caretPos

        if braceAtCaret >= 0:
            braceOpposite = self.BraceMatch(braceAtCaret)

        if braceAtCaret != -1  and braceOpposite == -1:
            self.BraceBadLight(braceAtCaret)
        else:
            self.BraceHighlight(braceAtCaret, braceOpposite)
            #pt = self.PointFromPosition(braceOpposite)
            #self.Refresh(True, wxRect(pt.x, pt.y, 5,5))
            #print pt
            #self.Refresh(False)
        

        if self.frame.prefs['showSourceAsst']:
            #check current word including .
            if charBefore== ord('('):
                startPos = self.WordStartPosition(caretPos-2, True)
                endPos = caretPos-1
            else:
                startPos = self.WordStartPosition(caretPos, True)
                endPos = self.WordEndPosition(caretPos, True)
            #extend starPos back to beginngin of class separated by .
            while self.GetCharAt(startPos-1)==ord('.'):
                startPos = self.WordStartPosition(startPos-1, True)
            #now retrieve word
            currWord = self.GetTextRange(startPos, endPos)
            
            #lookfor word in dictionary
            if currWord in self.autoCompleteDict.keys():
                helpText = self.autoCompleteDict[currWord]['help']
                thisIs = self.autoCompleteDict[currWord]['is']
                thisType = self.autoCompleteDict[currWord]['type']
                thisAttrs = self.autoCompleteDict[currWord]['attrs']
                if type(thisIs)==str:#if this is a module
                    searchFor = thisIs 
                else:
                    searchFor = currWord
            else:
                helpText = None
                thisIs=None
                thisAttrs=None
                thisType=None
                searchFor = currWord
                
            
            if self.prevWord != currWord:
                #if we have a class or function then use introspect (because it retrieves args as well as __doc__)
                if thisType is not 'instance':
                    wd, kwArgs, helpText = introspect.getCallTip(searchFor, locals=self.locals)
                #then pass all info to sourceAsst
                self.updateSourceAsst(currWord, thisIs, helpText, thisType, thisAttrs)#for an instance inclue known attrs
                    
                self.prevWord = currWord#update for next time
                
    def updateSourceAsst(self,currWord, thisIs, helpText, thisType=None, knownAttrs=None):
            #update the source assistant window            
            sa = self.frame.sourceAsstWindow
            assert isinstance(sa, wx.richtext.RichTextCtrl)
            # clear the buffer
            sa.Clear()
            
            #add current symbol
            sa.BeginBold()
            sa.WriteText('Symbol: ')
            sa.BeginTextColour('BLUE')
            sa.WriteText(currWord+'\n')
            sa.EndTextColour()
            sa.EndBold()
            
            #add expected type
            sa.BeginBold()
            sa.WriteText('is: ')
            sa.EndBold()
            if thisIs: sa.WriteText(str(thisIs)+'\n')
            else: sa.WriteText('\n')
            
            #add expected type
            sa.BeginBold()
            sa.WriteText('type: ')
            sa.EndBold()
            if thisIs: sa.WriteText(str(thisType)+'\n')
            else: sa.WriteText('\n')
                
            #add help text
            sa.BeginBold()
            sa.WriteText('Help:\n')
            sa.EndBold()
            if helpText: sa.WriteText(helpText+'\n')
            else: sa.WriteText('\n')
            
            #add attrs
            sa.BeginBold()
            sa.WriteText('Known methods:\n')
            sa.EndBold()
            if knownAttrs: 
                if len(knownAttrs)>500:
                    sa.WriteText('\ttoo many to list (i.e. more than 500)!!\n')
                else:
                    for thisAttr in knownAttrs:
                        sa.WriteText('\t'+thisAttr+'\n')
            else: sa.WriteText('\n')
                
    def OnMarginClick(self, evt):
        # fold and unfold as needed
        if evt.GetMargin() == 2:
            if evt.GetShift() and evt.GetControl():
                self.FoldAll()
            else:
                lineClicked = self.LineFromPosition(evt.GetPosition())

                if self.GetFoldLevel(lineClicked) & wx.stc.STC_FOLDLEVELHEADERFLAG:
                    if evt.GetShift():
                        self.SetFoldExpanded(lineClicked, True)
                        self.Expand(lineClicked, True, True, 1)
                    elif evt.GetControl():
                        if self.GetFoldExpanded(lineClicked):
                            self.SetFoldExpanded(lineClicked, False)
                            self.Expand(lineClicked, False, True, 0)
                        else:
                            self.SetFoldExpanded(lineClicked, True)
                            self.Expand(lineClicked, True, True, 100)
                    else:
                        self.ToggleFold(lineClicked)


    def FoldAll(self):
        lineCount = self.GetLineCount()
        expanding = True
        
        # find out if we are folding or unfolding
        for lineNum in range(lineCount):
            if self.GetFoldLevel(lineNum) & wx.stc.STC_FOLDLEVELHEADERFLAG:
                expanding = not self.GetFoldExpanded(lineNum)
                break

        lineNum = 0

        while lineNum < lineCount:
            level = self.GetFoldLevel(lineNum)
            if level & wx.stc.STC_FOLDLEVELHEADERFLAG and \
               (level & wx.stc.STC_FOLDLEVELNUMBERMASK) == wx.stc.STC_FOLDLEVELBASE:

                if expanding:
                    self.SetFoldExpanded(lineNum, True)
                    lineNum = self.Expand(lineNum, True)
                    lineNum = lineNum - 1
                else:
                    lastChild = self.GetLastChild(lineNum, -1)
                    self.SetFoldExpanded(lineNum, False)

                    if lastChild > lineNum:
                        self.HideLines(lineNum+1, lastChild)

            lineNum = lineNum + 1



    def Expand(self, line, doExpand, force=False, visLevels=0, level=-1):
        lastChild = self.GetLastChild(line, level)
        line = line + 1
        
        while line <= lastChild:
            if force:
                if visLevels > 0:
                    self.ShowLines(line, line)
                else:
                    self.HideLines(line, line)
            else:
                if doExpand:
                    self.ShowLines(line, line)

            if level == -1:
                level = self.GetFoldLevel(line)

            if level & wx.stc.STC_FOLDLEVELHEADERFLAG:
                if force:
                    if visLevels > 1:
                        self.SetFoldExpanded(line, True)
                    else:
                        self.SetFoldExpanded(line, False)

                    line = self.Expand(line, doExpand, force, visLevels-1)

                else:
                    if doExpand and self.GetFoldExpanded(line):
                        line = self.Expand(line, True, force, visLevels-1)
                    else:
                        line = self.Expand(line, False, force, visLevels-1)
            else:
                line = line + 1

        return line

    
    def commentLines(self):
        #used for the comment/uncomment machinery from ActiveGrid
        newText = ""
        for lineNo in self._GetSelectedLineNumbers():
            lineText = self.GetLine(lineNo)
            if (len(lineText) > 1 and lineText[0] == '#') or (len(lineText) > 2 and lineText[:2] == '##'):
                newText = newText + lineText
            else:
                newText = newText + "#" + lineText
        self._ReplaceSelectedLines(newText)
    def uncommentLines(self):
        #used for the comment/uncomment machinery from ActiveGrid
        newText = ""
        for lineNo in self._GetSelectedLineNumbers():
            lineText = self.GetLine(lineNo)
            if len(lineText) >= 2 and lineText[:2] == "#":
                lineText = lineText[2:]
            elif len(lineText) >= 1 and lineText[:1] == "#":
                lineText = lineText[1:]
            newText = newText + lineText
        self._ReplaceSelectedLines(newText)
    def _GetSelectedLineNumbers(self):
        #used for the comment/uncomment machinery from ActiveGrid
        selStart, selEnd = self._GetPositionsBoundingSelectedLines()
        return range(self.LineFromPosition(selStart), self.LineFromPosition(selEnd))
    def _GetPositionsBoundingSelectedLines(self):
        #used for the comment/uncomment machinery from ActiveGrid
        startPos = self.GetCurrentPos()
        endPos = self.GetAnchor()
        if startPos > endPos:
            temp = endPos
            endPos = startPos
            startPos = temp
        if endPos == self.PositionFromLine(self.LineFromPosition(endPos)):
            endPos = endPos - 1  # If it's at the very beginning of a line, use the line above it as the ending line
        selStart = self.PositionFromLine(self.LineFromPosition(startPos))
        selEnd = self.PositionFromLine(self.LineFromPosition(endPos) + 1)
        return selStart, selEnd
    def _ReplaceSelectedLines(self, text):
        #used for the comment/uncomment machinery from ActiveGrid
        if len(text) == 0:
            return
        selStart, selEnd = self._GetPositionsBoundingSelectedLines()
        self.SetSelection(selStart, selEnd)
        self.ReplaceSelection(text)
        self.SetSelection(selStart + len(text), selStart)    
        
    def analyseScript(self):
        #analyse the file
        buffer = StringIO.StringIO()
        buffer.write(self.GetText())
        buffer.seek(0)
        try:
            importStatements, tokenDict = psychoParser.getTokensAndImports(buffer)
            successfulParse=True
        except:
            successfulParse=False
        buffer.close()
        
        if successfulParse: #if we parsed the tokens then process them
            
            #import the libs used by the script
            if self.frame.modulesLoaded:
                for thisLine in importStatements:
                    #check what file we're importing from
                    tryImport=ALLOW_MODULE_IMPORTS
                    words = string.split(thisLine)
                    for word in words:#don't import from files in this folder (user files)
                        if os.path.isfile(word+'.py'):
                            tryImport=False
                    if tryImport:
                        try:#it might not import
                            exec(thisLine)
                        except:
                            pass
                    self.locals = locals()#keep a track of our new locals
                self.autoCompleteDict = {}
                
            #go through imported symbols (using dir())
            #loop through to appropriate level of module tree getting all possible symbols
            symbols = dir()
            #remove some tokens that are just from here
            symbols.remove('self')
            symbols.remove('buffer')
            symbols.remove('tokenDict')
            symbols.remove('successfulParse')
            for thisSymbol in symbols:
                #create an actual obj from the name
                exec('thisObj=%s' %thisSymbol)
                #(try to) get the attributes of the object
                try:
                    newAttrs = dir(thisObj)
                except:
                    newAttrs=[]
                    
                #only dig deeper if we haven't exceeded the max level of analysis
                if thisSymbol.find('.') < self.frame.prefs['analysisLevel']:
                    #we should carry on digging deeper
                    for thisAttr in newAttrs:
                        #by appending the symbol it will also get analysed!
                        symbols.append(thisSymbol+'.'+thisAttr)
                        
                #but (try to) add data for all symbols including this level
                try:
                    self.autoCompleteDict[thisSymbol]={'is':thisObj,
                        'type':type(thisObj),
                        'attrs':newAttrs,
                        'help':thisObj.__doc__}
                except:
                    pass#not sure what happened - maybe no __doc__?
            
            #add keywords    
            for thisName in keyword.kwlist[:]:
                self.autoCompleteDict[thisName]={'is':'Keyword','type':'Keyword', 'attrs':None, 'help':None}
            self.autoCompleteDict['self']={'is':'self','type':'self', 'attrs':None, 'help':None}
            
            #then add the tokens (i.e. instances) from this script
            for thisKey in tokenDict:
                #the default is to have no fields filled
                thisObj= thisIs = thisHelp = thisType = thisAttrs = None
                keyIsStr = tokenDict[thisKey]['is']
                try:
                    exec('thisObj=%s' %keyIsStr)
                    if type(thisObj)==types.FunctionType:
                        thisIs = 'returned from functon'
                    else:
                        thisIs = str(thisObj)
                        thisType = 'instance'
                        thisHelp = thisObj.__doc__
                        thisAttrs = dir(thisObj)
                except:
                    pass
                self.autoCompleteDict[thisKey]={'is':thisIs,
                    'type':thisType,
                    'attrs':thisAttrs,
                    'help':thisHelp}
                
    def onModified(self, event):
        #update the UNSAVED flag and the save icons
        notebook = self.GetParent()
        mainFrame = notebook.GetParent()
        mainFrame.setFileModified(True)
    def DoFindNext(self, findData, findDlg=None):
        #this comes straight from wx.py.editwindow  (which is a subclass of STC control)
        backward = not (findData.GetFlags() & wx.FR_DOWN)
        matchcase = (findData.GetFlags() & wx.FR_MATCHCASE) != 0
        end = self.GetLength()
        textstring = self.GetTextRange(0, end)
        findstring = findData.GetFindString()
        if not matchcase:
            textstring = textstring.lower()
            findstring = findstring.lower()
        if backward:
            start = self.GetSelection()[0]
            loc = textstring.rfind(findstring, 0, start)
        else:
            start = self.GetSelection()[1]
            loc = textstring.find(findstring, start)

        # if it wasn't found then restart at begining
        if loc == -1 and start != 0:
            if backward:
                start = end
                loc = textstring.rfind(findstring, 0, start)
            else:
                start = 0
                loc = textstring.find(findstring, start)

        # was it still not found?
        if loc == -1:
            dlg = wx.MessageDialog(self, 'Unable to find the search text.',
                          'Not found!',
                          wx.OK | wx.ICON_INFORMATION)
            dlg.ShowModal()
            dlg.Destroy()
        else:               
            # show and select the found text
            line = self.LineFromPosition(loc)
            #self.EnsureVisible(line)
            self.GotoLine(line)
            self.SetSelection(loc, loc + len(findstring))
        if findDlg:
            if loc == -1:
                wx.CallAfter(findDlg.SetFocus)
                return
            else:
                findDlg.Close()

        
        
class StdOutRich(wx.richtext.RichTextCtrl):
    def __init__(self, parent, style, size):
        wx.richtext.RichTextCtrl.__init__(self,parent=parent, style=style, size=size)
        self.Bind(wx.EVT_TEXT_URL, self.OnURL)
        self.parent=parent
        self.SetScrollPageSize( wx.PORTRAIT, 1000)
        #define style for filename links (URLS) needs wx as late as 2.8.4.0
        #self.urlStyle = wx.richtext.RichTextAttr()
        #self.urlStyle.SetTextColour(wx.BLUE)
        #self.urlStyle.SetFontWeight(wx.BOLD)
        #self.urlStyle.SetFontUnderlined(False)
        
        self.write('Welcome to the Integrated Development Environment (IDE) for PsychoPy!\n')
        self.write("v%s\n" %self.parent.app.version)
        
    def write(self,inStr):
        self.MoveEnd()#always 'append' text rather than 'writing' it
        """tracebacks have the form:
        Traceback (most recent call last):
        File "C:\Program Files\wxPython2.8 Docs and Demos\samples\hangman\hangman.py", line 21, in <module>
            class WordFetcher:
        File "C:\Program Files\wxPython2.8 Docs and Demos\samples\hangman\hangman.py", line 23, in WordFetcher
        """
        for thisLine in inStr.splitlines(True):
            if len(re.findall('".*", line.*',thisLine))>0:
                #this line contains a file/line location so write as URL 
                #self.BeginStyle(self.urlStyle) #this should be done with styles, but they don't exist in wx as late as 2.8.4.0
                self.BeginBold()
                self.BeginTextColour(wx.BLUE)
                self.BeginURL(thisLine)
                self.WriteText(thisLine)
                self.EndURL()
                self.EndBold()
                self.EndTextColour()
            else:
                #line to write as simple text
                self.WriteText(thisLine)
        self.MoveEnd()#go to end of stdout so user can see updated text
        self.ShowPosition(self.GetLastPosition() )
    def OnURL(self, evt):
        """decompose the URL of a file and line number"""
        # "C:\\Program Files\\wxPython2.8 Docs and Demos\\samples\\hangman\\hangman.py", line 21,
        filename = evt.GetString().split('"')[1]
        lineNumber = int(evt.GetString().split(',')[1][5:])
        self.parent.gotoLine(filename,lineNumber)
    def flush(self):
        pass#not needed?
        
#def makeAccelTable():
#    table = wx.AcceleratorTable([ \
#        (wx.ACCEL_CTRL, ord('Q'), ID_EXIT),
#        (wx.ACCEL_CTRL, ord('S'), wx.ID_SAVE),
#        (wx.ACCEL_NORMAL,  wx.WXK_F5, ID_RUNFILE),
#        (wx.ACCEL_ALT,  wx.WXK_HOME, ID_FOLDALL),
#        (wx.ACCEL_CTRL,  ord(']'), ID_INDENT),#doesn't work on windwos - handle as a keypress in text editor
#        (wx.ACCEL_CTRL,  ord('['), ID_DEDENT),
#        (wx.ACCEL_CTRL,  ord('/'), ID_COMMENT),#doesn't work on windwos - handle as a keypress in text editor
##        (wx.ACCEL_CTRL,  ord('D'), wx.ID_DUPLICATE),#this is automatic in StyledTextCtrl anyway?
##        (wx.ACCEL_CTRL,  ord('Z'), wx.ID_UNDO),#this is automatic in StyledTextCtrl anyway?
##        (wx.ACCEL_CTRL,  ord('Y'), wx.ID_REDO),#this is automatic in StyledTextCtrl anyway?
#    ])
#    return table
#   

class CoderFrame(wx.Frame):
    def __init__(self, parent, ID, title, files=[], app=None):
        self.app = app
        self.appData = self.app.prefs.appData['coder']#things the user doesn't set like winsize etc
        self.prefs = self.app.prefs.coder#things about the coder that get set
        self.appPrefs = self.app.prefs.app
        self.paths = self.app.prefs.paths
        self.IDs = self.app.IDs
        self.currentDoc=None
        self.ignoreErrors = False
#        print self.appData
        if self.appData['winH']==0 or self.appData['winW']==0:#we didn't have the key or the win was minimized/invalid
            self.appData['winH'], self.appData['winH'] =wx.DefaultSize
            self.appData['winX'],self.appData['winY'] =wx.DefaultPosition
        wx.Frame.__init__(self, parent, ID, title,
                         (self.appData['winX'], self.appData['winY']),
                         size=(self.appData['winW'],self.appData['winH']))
        self.panel = wx.Panel(self)      
        self.Hide()#ugly to see it all initialise
        #create icon
        if sys.platform=='darwin':
            pass#doesn't work and not necessary - handled by application bundle
        else:
            iconFile = os.path.join(self.paths['resources'], 'psychopy.ico')
            if os.path.isfile(iconFile):
                self.SetIcon(wx.Icon(iconFile, wx.BITMAP_TYPE_ICO))
        wx.EVT_CLOSE(self, self.closeFrame)#NB not the same as quit - just close the window
        wx.EVT_IDLE(self, self.onIdle)
#        self.SetAcceleratorTable(makeAccelTable())
        if self.appData.has_key('state') and self.appData['state']=='maxim':
            self.Maximize()
        #initialise some attributes
        self.modulesLoaded=False #will turn true when loading thread completes
        self.findDlg = None
        self.findData = wx.FindReplaceData()
        self.findData.SetFlags(wx.FR_DOWN)
        self.importedScripts={}
        self.scriptProcess=None
        self.scriptProcessID=None
        self.db = None#debugger
        self._lastCaretPos=None
        
        #setup statusbar
        self.CreateStatusBar()
        self.SetStatusText("")
        self.fileMenu = self.editMenu = self.viewMenu = self.helpMenu = self.toolsMenu = None
        
        #make the pane manager
        self.paneManager = wx.aui.AuiManager()
                
        #create an editor pane
        self.paneManager.SetFlags(wx.aui.AUI_MGR_RECTANGLE_HINT)
        self.paneManager.SetManagedWindow(self)
        #make the notebook
#        self.notebook = wx.Notebook(self, -1,size=wx.Size(200,200)) #size doesn't make any difference!, size=wx.Size(600,12000))
        self.notebook = wx.aui.AuiNotebook(self, -1, size=wx.Size(600,600), 
            style= wx.aui.AUI_NB_TOP | wx.aui.AUI_NB_TAB_SPLIT | wx.aui.AUI_NB_SCROLL_BUTTONS | \
                wx.aui.AUI_NB_TAB_MOVE | wx.aui.AUI_NB_CLOSE_ON_ACTIVE_TAB | wx.aui.AUI_NB_WINDOWLIST_BUTTON)
        
        self.paneManager.AddPane(self.notebook, wx.aui.AuiPaneInfo().
                          Name("Editor").Caption("Editor").
                          CenterPane(). #'center panes' expand to fill space
                          CloseButton(False).MaximizeButton(True))

        self.notebook.SetFocus()        

        self.Bind(wx.aui.EVT_AUINOTEBOOK_PAGE_CLOSE, self.fileClose)
        self.Bind(wx.aui.EVT_AUINOTEBOOK_PAGE_CHANGED, self.pageChanged)
        #self.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.pageChanged)
        self.DragAcceptFiles(True)
        self.Bind(wx.EVT_DROP_FILES, self.filesDropped)        
        self.Bind(wx.EVT_FIND, self.OnFindNext)
        self.Bind(wx.EVT_FIND_NEXT, self.OnFindNext)
        self.Bind(wx.EVT_FIND_CLOSE, self.OnFindClose)
        self.Bind(wx.EVT_END_PROCESS, self.onProcessEnded)        
        
        #for demos we need a dict where the event ID will correspond to a filename
        self.demoList = glob.glob(os.path.join(self.paths['demos'],'*.py'))
        #demoList = glob.glob(os.path.join(appDir,'..','demos','*.py'))
        self.ID_DEMOS = \
            map(lambda _makeID: wx.NewId(), range(len(self.demoList)))
        self.demos={}
        for n in range(len(self.demoList)):
            self.demos[self.ID_DEMOS[n]] = self.demoList[n]
            
        self.makeMenus()
        self.makeToolbar()
        
        #take files from arguments and append the previously opened files
        if files: 
            print 'files:', files
            self.appData['prevFiles'].extend(files)
        if len(self.appData['prevFiles'])==0:
            #then no files previously opened
            self.setCurrentDoc('', keepHidden=True) #a dummy page to start
        else:
            #re-open previous files
            for filename in self.appData['prevFiles']: 
                if not os.path.isfile(filename): continue
                self.setCurrentDoc(filename, keepHidden=True)      
                
        #create output viewer
        self._origStdOut = sys.stdout#keep track of previous output
        self._origStdErr = sys.stderr
        self.outputWindow = StdOutRich(self,style=wx.TE_MULTILINE|wx.TE_READONLY, size=wx.Size(400,400))
        self.paneManager.AddPane(self.outputWindow, 
                                 wx.aui.AuiPaneInfo().
                                 Name("Output").Caption("Output").
                                 RightDockable(True).LeftDockable(True).CloseButton(False).
                                 Bottom())
        #will we show the pane straight away?
        self.setOutputWindow(event=None)
        
        #add help window
        self.sourceAsstWindow = wx.richtext.RichTextCtrl(self,-1, size=wx.Size(300,300), 
                                          style=wx.TE_MULTILINE|wx.TE_READONLY)
        self.paneManager.AddPane(self.sourceAsstWindow, 
                                 wx.aui.AuiPaneInfo().BestSize((600,600)).
                                 Name("SourceAsst").Caption("Source Assistant").
                                 RightDockable(True).LeftDockable(True).CloseButton(False).
                                 Right())
        #will we show the pane straight away?
        if self.prefs['showSourceAsst']:
            self.paneManager.GetPane('SourceAsst').Show()
        else:self.paneManager.GetPane('SourceAsst').Hide()
        
        #self.SetSizer(self.mainSizer)#not necessary for aui type controls
        if self.appData['auiPerspective']:
            self.paneManager.LoadPerspective(self.appData['auiPerspective'])
        else:
            self.SetMinSize(wx.Size(800, 800)) #min size for the whole window
            self.Fit()
            self.paneManager.Update()
            self.SetMinSize(wx.Size(200, 200)) #min size for the whole window
        self.SendSizeEvent()
        
        self.Show()#now it's all done
        
    def makeMenus(self):
        #---Menus---#000000#FFFFFF--------------------------------------------------
        menuBar = wx.MenuBar()
        #---_file---#000000#FFFFFF--------------------------------------------------
        self.fileMenu = wx.Menu()
        menuBar.Append(self.fileMenu, '&File')
        
        #create a file history submenu
        self.fileHistory = wx.FileHistory()
        self.recentFilesMenu = wx.Menu()
        self.fileHistory.UseMenu(self.recentFilesMenu)
        for filename in self.appData['fileHistory']: self.fileHistory.AddFileToHistory(filename)
        self.Bind(
            wx.EVT_MENU_RANGE, self.OnFileHistory, id=wx.ID_FILE1, id2=wx.ID_FILE9
            )
            
        #add items to file menu    
        self.fileMenu.Append(wx.ID_NEW,     "&New\t%s" %self.app.keys.new)
        self.fileMenu.Append(wx.ID_OPEN,    "&Open...\t%s" %self.app.keys.open)
        self.fileMenu.AppendSubMenu(self.recentFilesMenu,"Open &Recent")
        self.fileMenu.Append(wx.ID_SAVE,    "&Save\t%s" %self.app.keys.save)
        self.fileMenu.Append(wx.ID_SAVEAS,  "Save &as...\t%s" %self.app.keys.saveAs)
        self.fileMenu.Append(wx.ID_CLOSE,   "&Close file\t%s" %self.app.keys.close)
        wx.EVT_MENU(self, wx.ID_NEW,  self.fileNew)
        wx.EVT_MENU(self, wx.ID_OPEN,  self.fileOpen)
        wx.EVT_MENU(self, wx.ID_SAVE,  self.fileSave)
        self.fileMenu.Enable(wx.ID_SAVE, False)
        wx.EVT_MENU(self, wx.ID_SAVEAS,  self.fileSaveAs)
        wx.EVT_MENU(self, wx.ID_CLOSE,  self.fileClose)
        item = self.fileMenu.Append(wx.ID_PREFERENCES, text = "&Preferences")
        self.Bind(wx.EVT_MENU, self.app.showPrefs, item)
        #-------------quit
        self.fileMenu.AppendSeparator()
        self.fileMenu.Append(wx.ID_EXIT, "&Quit\t%s" %self.app.keys.quit, "Terminate the program")
        wx.EVT_MENU(self, wx.ID_EXIT, self.quit)
        
        #---_edit---#000000#FFFFFF--------------------------------------------------
        self.editMenu = wx.Menu()
        menuBar.Append(self.editMenu, '&Edit')
        self.editMenu.Append(self.IDs.cut, "Cu&t\t%s" %self.app.keys.cut)
        wx.EVT_MENU(self, self.IDs.cut,  self.cut)
        self.editMenu.Append(self.IDs.copy, "&Copy\t%s" %self.app.keys.copy)
        wx.EVT_MENU(self, self.IDs.copy,  self.copy)
        self.editMenu.Append(self.IDs.paste, "&Paste\t%s" %self.app.keys.paste)
        wx.EVT_MENU(self, self.IDs.paste,  self.paste)
        self.editMenu.Append(wx.ID_DUPLICATE, "&Duplicate\t%s" %self.app.keys.duplicate, "Duplicate the current line (or current selection)")
        wx.EVT_MENU(self, wx.ID_DUPLICATE,  self.duplicateLine)
        
        self.editMenu.AppendSeparator()
        self.editMenu.Append(self.IDs.showFind, "&Find\t%s" %self.app.keys.find)
        wx.EVT_MENU(self, self.IDs.showFind, self.OnFindOpen)
        self.editMenu.Append(self.IDs.findNext, "Find &Next\t%s" %self.app.keys.findAgain)
        wx.EVT_MENU(self, self.IDs.findNext, self.OnFindNext)
        
        self.editMenu.AppendSeparator()
        self.editMenu.Append(self.IDs.comment, "Comment\t%s" %self.app.keys.comment, "Comment selected lines", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.comment,  self.commentSelected)
        self.editMenu.Append(self.IDs.unComment, "Uncomment\t%s" %self.app.keys.uncomment, "Un-comment selected lines", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.unComment,  self.uncommentSelected)       
        self.editMenu.Append(self.IDs.foldAll, "Toggle fold\t%s" %self.app.keys.fold, "Toggle folding of top level", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.foldAll,  self.foldAll)  
        
        self.editMenu.AppendSeparator()
        self.editMenu.Append(self.IDs.indent, "Indent selection\t%s" %self.app.keys.indent, "Increase indentation of current line", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.indent,  self.indent)
        self.editMenu.Append(self.IDs.dedent, "Dedent selection\t%s" %self.app.keys.dedent, "Decrease indentation of current line", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.dedent,  self.dedent)
        self.editMenu.Append(self.IDs.smartIndent, "SmartIndent\t%s" %self.app.keys.smartIndent, "Try to indent to the correct position w.r.t  last line", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, self.IDs.smartIndent,  self.smartIndent)
        
        self.editMenu.AppendSeparator()
        self.editMenu.Append(wx.ID_UNDO, "Undo\t%s" %self.app.keys.undo, "Undo last action", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, wx.ID_UNDO,  self.undo)
        self.editMenu.Append(wx.ID_REDO, "Redo\t%s" %self.app.keys.redo, "Redo last action", wx.ITEM_NORMAL)
        wx.EVT_MENU(self, wx.ID_REDO,  self.redo)
        
        #self.editMenu.Append(ID_UNFOLDALL, "Unfold All\tF3", "Unfold all lines", wx.ITEM_NORMAL)
        #wx.EVT_MENU(self, ID_UNFOLDALL,  self.unfoldAll)
        #---_tools---#000000#FFFFFF--------------------------------------------------
        self.toolsMenu = wx.Menu()
        menuBar.Append(self.toolsMenu, '&Tools')
        self.toolsMenu.Append(self.IDs.openMonCentre, "Monitor Center", "To set information about your monitor")
        wx.EVT_MENU(self, self.IDs.openMonCentre,  self.openMonitorCenter)
        self.analyseAutoChk = self.toolsMenu.AppendCheckItem(self.IDs.analyzeAuto, "Analyse on file save/open", "Automatically analyse source (for autocomplete etc...). Can slow down the editor on a slow machine or with large files")
        wx.EVT_MENU(self, self.IDs.analyzeAuto,  self.setAnalyseAuto)
        self.analyseAutoChk.Check(self.prefs['analyseAuto'])
        self.toolsMenu.Append(self.IDs.analyzeNow, "Analyse now\t%s" %self.app.keys.analyseCode, "Force a reananalysis of the code now")
        wx.EVT_MENU(self, self.IDs.analyzeNow,  self.analyseCodeNow)
        
        self.toolsMenu.Append(self.IDs.runFile, "Run\t%s" %self.app.keys.runScript, "Run the current script")
        wx.EVT_MENU(self, self.IDs.runFile,  self.runFile)        
        self.toolsMenu.Append(self.IDs.stopFile, "Stop\t%s" %self.app.keys.stopScript, "Run the current script")
        wx.EVT_MENU(self, self.IDs.stopFile,  self.stopFile)

        
        #---_view---#000000#FFFFFF--------------------------------------------------
        self.viewMenu = wx.Menu()
        menuBar.Append(self.viewMenu, '&View')        
        #output window
        self.outputChk= self.viewMenu.AppendCheckItem(self.IDs.toggleOutput, "&Output",
                                                  "shows the output (and error messages) from your script")
        self.outputChk.Check(self.prefs['showOutput'])
        wx.EVT_MENU(self, self.IDs.toggleOutput,  self.setOutputWindow)        
        #source assistant
        self.sourceAsstChk= self.viewMenu.AppendCheckItem(self.IDs.toggleSourceAsst, "&Source Assistant",
                                                  "Provides help functions and attributes of classes in your script")
        self.sourceAsstChk.Check(self.prefs['showSourceAsst'])
        wx.EVT_MENU(self, self.IDs.toggleSourceAsst,  self.setSourceAsst)
        self.viewMenu.AppendSeparator()       
        self.viewMenu.Append(self.IDs.openBuilderView, "&Open Bulder view\t%s" %self.app.keys.switchToBuilder, "Open a new Builder view")
        wx.EVT_MENU(self, self.IDs.openBuilderView,  self.app.newBuilderFrame)
                
        
        #---_help---#000000#FFFFFF--------------------------------------------------
        self.helpMenu = wx.Menu()
        menuBar.Append(self.helpMenu, '&Help') 
        self.helpMenu.Append(self.IDs.psychopyHome, "&PsychoPy Homepage", "Go to the PsychoPy homepage")
        wx.EVT_MENU(self, self.IDs.psychopyHome, self.app.followLink)
        self.helpMenu.Append(self.IDs.psychopyTutorial, "&PsychoPy Tutorial", "Go to the online PsychoPy tutorial")
        wx.EVT_MENU(self, self.IDs.psychopyTutorial, self.app.followLink)
        
        self.demosMenu = wx.Menu()
        menuBar.Append(self.demosMenu, '&Demos') 
        for thisID in self.ID_DEMOS:
            junk, shortname = os.path.split(self.demos[thisID])
            if shortname=="__init__.py": continue
            self.demosMenu.Append(thisID, shortname)
            wx.EVT_MENU(self, thisID, self.loadDemo)
        
        self.helpMenu.AppendSeparator()       
        self.helpMenu.Append(wx.ID_ABOUT, "&About...", "About PsychoPy")#on mac this will move to appication menu
        wx.EVT_MENU(self, wx.ID_ABOUT, self.app.showAbout)
        self.helpMenu.Append(self.IDs.license, "License...", "PsychoPy License")
        wx.EVT_MENU(self, self.IDs.license, self.app.showLicense)
        
        self.SetMenuBar(menuBar)
        
    def makeToolbar(self):
        #---toolbar---#000000#FFFFFF----------------------------------------------
        self.toolbar = self.CreateToolBar( (wx.TB_HORIZONTAL
            | wx.NO_BORDER
            | wx.TB_FLAT))
            
        if sys.platform=='win32' or sys.platform.startswith('linux'):
            if self.prefs['largeIcons']: toolbarSize=32         
            else: toolbarSize=16
        else:
            toolbarSize=32 #size 16 doesn't work on mac wx
        self.toolbar.SetToolBitmapSize((toolbarSize,toolbarSize))
        new_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'filenew%i.png' %toolbarSize), wx.BITMAP_TYPE_PNG)
        open_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'fileopen%i.png' %toolbarSize), wx.BITMAP_TYPE_PNG)
        save_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'filesave%i.png' %toolbarSize), wx.BITMAP_TYPE_PNG)
        saveAs_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'filesaveas%i.png' %toolbarSize), wx.BITMAP_TYPE_PNG)
        undo_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'undo%i.png' %toolbarSize),wx.BITMAP_TYPE_PNG)
        redo_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'redo%i.png' %toolbarSize),wx.BITMAP_TYPE_PNG)
        stop_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'stop%i.png' %toolbarSize),wx.BITMAP_TYPE_PNG)
        run_bmp = wx.Bitmap(os.path.join(self.paths['resources'], 'run%i.png' %toolbarSize),wx.BITMAP_TYPE_PNG)
        
        self.toolbar.AddSimpleTool(self.IDs.tbFileNew, new_bmp, "New [Ctrl+N]", "Create new python file")
        self.toolbar.Bind(wx.EVT_TOOL, self.fileNew, id=self.IDs.tbFileNew)
        self.toolbar.AddSimpleTool(self.IDs.tbFileOpen, open_bmp, "Open [Ctrl+O]", "Open an existing file'")
        self.toolbar.Bind(wx.EVT_TOOL, self.fileOpen, id=self.IDs.tbFileOpen)
        self.toolbar.AddSimpleTool(self.IDs.tbFileSave, save_bmp, "Save [Ctrl+S]", "Save current file")        
        self.toolbar.EnableTool(self.IDs.tbFileSave, False)
        self.toolbar.Bind(wx.EVT_TOOL, self.fileSave, id=self.IDs.tbFileSave)
        self.toolbar.AddSimpleTool(self.IDs.tbFileSaveAs, saveAs_bmp, "Save As... [Ctrl+Shft+S]", "Save current python file as...")
        self.toolbar.Bind(wx.EVT_TOOL, self.fileSaveAs, id=self.IDs.tbFileSaveAs)
        self.toolbar.AddSimpleTool(self.IDs.tbUndo, undo_bmp, "Undo [Ctrl+U]", "Undo last action")
        self.toolbar.Bind(wx.EVT_TOOL, self.undo, id=self.IDs.tbUndo)
        self.toolbar.AddSimpleTool(self.IDs.tbRedo, redo_bmp, "Redo [Ctrl+R]", "Redo last action")
        self.toolbar.Bind(wx.EVT_TOOL, self.redo, id=self.IDs.tbRedo)
        self.toolbar.AddSeparator()
        self.toolbar.AddSimpleTool(self.IDs.tbRun, run_bmp, "Run [F5]",  "Run current script")
        self.toolbar.Bind(wx.EVT_TOOL, self.runFile, id=self.IDs.tbRun)
        self.toolbar.AddSimpleTool(self.IDs.tbStop, stop_bmp, "Stop [Shift+F5]",  "Stop current script")
        self.toolbar.Bind(wx.EVT_TOOL, self.stopFile, id=self.IDs.tbStop)
        self.toolbar.EnableTool(self.IDs.tbStop,False)
        self.toolbar.Realize()
    
    def onIdle(self, event):
        #check the script outputs to see if anything has been written to stdout
        if self.scriptProcess is not None:
            if self.scriptProcess.IsInputAvailable():
                stream = self.scriptProcess.GetInputStream()
                text = stream.read()
                self.outputWindow.write(text)
            if self.scriptProcess.IsErrorAvailable():
                stream = self.scriptProcess.GetErrorStream()
                text = stream.read()
                self.outputWindow.write(text)
        #check if we're in the same place as before
        if hasattr(self.currentDoc, 'GetCurrentPos') and (self._lastCaretPos!=self.currentDoc.GetCurrentPos()):
            self.currentDoc.OnUpdateUI(evt=None)
            self._lastCaretPos=self.currentDoc.GetCurrentPos()
    def pageChanged(self,event):
        old = event.GetOldSelection()
        new = event.GetSelection()
        self.currentDoc = self.notebook.GetPage(new)
        self.setFileModified(self.currentDoc.UNSAVED)
        self.SetLabel('PsychoPy IDE - %s' %self.currentDoc.filename)
        #event.Skip()
    def filesDropped(self, event):
        fileList = event.GetFiles()
        for filename in fileList:
            if os.path.isfile(filename):
                self.setCurrentDoc(filename)    
    def OnFindOpen(self, event):
        #open the find dialog if not already open
        if self.findDlg is not None:
            return
        win = wx.Window.FindFocus()
        self.findDlg = wx.FindReplaceDialog(win, self.findData, "Find",
                                            wx.FR_NOWHOLEWORD)
        self.findDlg.Show()
        
    def OnFindNext(self, event):
        #find the next occurence of text according to last find dialogue data
        if not self.findData.GetFindString():
            self.OnFindOpen(event)
            return
        self.currentDoc.DoFindNext(self.findData, self.findDlg)
        if self.findDlg is not None:
            self.OnFindClose(None)

    def OnFindClose(self, event):
        self.findDlg.Destroy()
        self.findDlg = None
    def OnFileHistory(self, evt=None):
        # get the file based on the menu ID
        fileNum = evt.GetId() - wx.ID_FILE1
        path = self.fileHistory.GetHistoryFile(fileNum)
        self.setCurrentDoc(path)#load the file
        # add it back to the history so it will be moved up the list
        self.fileHistory.AddFileToHistory(path)
        
    def gotoLine(self, filename=None, line=0):
        #goto a specific line in a specific file and select all text in it
        self.setCurrentDoc(filename)
        self.currentDoc.EnsureVisible(line)
        self.currentDoc.GotoLine(line)
        endPos = self.currentDoc.GetCurrentPos()
        
        self.currentDoc.GotoLine(line-1)
        stPos = self.currentDoc.GetCurrentPos()
        
        self.currentDoc.SetSelection(stPos,endPos)
        
    def getOpenFilenames(self):
        """Return the full filename of each open tab"""
        names=[]
        for ii in range(self.notebook.GetPageCount()):
            names.append(self.notebook.GetPage(ii).filename)
        return names
    
    def quit(self, event):
        self.app.quit()
        
    def closeFrame(self, event=None):
        """Close open windows, update prefs.appData (but don't save) and either 
        close the frame or hide it
        """
        self.Hide()#ugly to see it close all the files independently
        #undo
        sys.stdout = self._origStdOut#discovered during __init__
        sys.stderr = self._origStdErr
        
        #store current appData
        self.appData['prevFiles'] = []
        currFiles = self.getOpenFilenames()
        for thisFileName in currFiles:
            self.appData['prevFiles'].append(thisFileName)
        #get size and window layout info
        if self.IsIconized():
            self.Iconize(False)#will return to normal mode to get size info
            self.appData['state']='normal'
        elif self.IsMaximized():
            self.Maximize(False)#will briefly return to normal mode to get size info
            self.appData['state']='maxim'
        else:
            self.appData['state']='normal'
        self.appData['auiPerspective'] = self.paneManager.SavePerspective()
        self.appData['winW'], self.appData['winH']=self.GetSize()
        self.appData['winX'], self.appData['winY']=self.GetPosition() 
        if sys.platform=='darwin':
            self.appData['winH'] -= 39#for some reason mac wxpython <=2.8 gets this wrong (toolbar?)
        for ii in range(self.fileHistory.GetCount()):
            self.appData['fileHistory'].append(self.fileHistory.GetHistoryFile(ii))
        
        #close each file (so that we check for saving)
        for thisFileName in currFiles:#must do this for all files AFTER adding them to the list
            ok = self.fileClose(event=0, filename=thisFileName)#delete from end back
            if ok==-1:
                return -1 #user cancelled - don't quit
        
        self.Hide()#the user may not have quit, so keep the menubar open by just hiding the window
        
    def fileNew(self, event=None, filepath=""):
        self.setCurrentDoc(filepath)
    def findDocID(self, filename):
        #find the ID of the current doc
        for ii in range(self.notebook.GetPageCount()):
            if self.notebook.GetPage(ii).filename == filename:
                return ii
        return -1
    def setCurrentDoc(self, filename, keepHidden=False):
        #check if this file is already open
        docID=self.findDocID(filename)
        if docID>=0:
            self.currentDoc = self.notebook.GetPage(docID)
            self.notebook.SetSelection(docID)
        else:#create new page and load document
            #if there is only a placeholder document then close it
            if len(self.getOpenFilenames())==1 and len(self.currentDoc.GetText())==0 and self.currentDoc.filename=='untitled.py':
                self.fileClose('untitled.py')  
            
            #create an editor window to put the text in
            p = self.currentDoc = CodeEditor(self.notebook,-1, frame=self)
                
            #load text from document
            if os.path.isfile(filename):
                self.currentDoc.SetText(open(filename).read())
                self.fileHistory.AddFileToHistory(filename)
            else:
                self.currentDoc.SetText("")
            self.currentDoc.EmptyUndoBuffer()
            self.currentDoc.Colourise(0, -1)
            
            # line numbers in the margin
            self.currentDoc.SetMarginType(1, wx.stc.STC_MARGIN_NUMBER)
            self.currentDoc.SetMarginWidth(1, 32)
            if filename=="":
                filename=shortName='untitled.py'
            else:
                path, shortName = os.path.split(filename)
            self.notebook.AddPage(p, shortName)   
            if isinstance(self.notebook, wx.Notebook):
                self.notebook.ChangeSelection(len(self.getOpenFilenames())-1)
            elif isinstance(self.notebook, wx.aui.AuiNotebook):
                self.notebook.SetSelection(len(self.getOpenFilenames())-1)
            self.currentDoc.filename=filename
            self.setFileModified(False)
        
        self.SetLabel('PsychoPy IDE - %s' %self.currentDoc.filename)
        if self.prefs['analyseAuto'] and len(self.getOpenFilenames())>0:
            self.SetStatusText('Analysing code')
            self.currentDoc.analyseScript()
            self.SetStatusText('')
        if not keepHidden:
            self.Show()#if the user had closed the frame it might be hidden
    def fileOpen(self, event):
        
        #get path of current file (empty if current file is '')
        if hasattr(self.currentDoc, 'filename'):
            initPath = os.path.split(self.currentDoc.filename)[0]
        else:   
            initPath=''
        dlg = wx.FileDialog(
            self, message="Open file ...", 
            defaultDir=initPath, style=wx.OPEN
            )
        
        if dlg.ShowModal() == wx.ID_OK:
            newPath = dlg.GetPath()
            self.SetStatusText('Loading file')
            self.setCurrentDoc(newPath)
            self.setFileModified(False)        
                    
        self.SetStatusText('')
        #self.fileHistory.AddFileToHistory(newPath)#thisis done by setCurrentDoc
    def fileSave(self,event, filename=None):
        
        if self.currentDoc.AutoCompActive():
            self.currentDoc.AutoCompCancel()
            
        if filename==None: 
            filename = self.currentDoc.filename
        if filename=='untitled.py':
            self.fileSaveAs(filename)
        else:
            self.SetStatusText('Saving file')
            f = open(filename,'w')
            f.write( self.currentDoc.GetText().encode('utf-8'))
            f.close()
        self.setFileModified(False)
            
        if self.prefs['analyseAuto'] and len(self.getOpenFilenames())>0:
            self.SetStatusText('Analysing current source code')
            self.currentDoc.analyseScript()
        #reset status text
        self.SetStatusText('')
        self.fileHistory.AddFileToHistory(filename)
        
    def fileSaveAs(self,event, filename=None):
                    
        if self.currentDoc.AutoCompActive():
            self.currentDoc.AutoCompCancel()
            
        if filename==None: filename = self.currentDoc.filename
        initPath, filename = os.path.split(filename)
        os.getcwd()
        if sys.platform=='darwin':
            wildcard="Python scripts (*.py)|*.py|Text file (*.txt)|*.txt|Any file (*.*)|*"
        else:
            wildcard="Python scripts (*.py)|*.py|Text file (*.txt)|*.txt|Any file (*.*)|*.*"

        dlg = wx.FileDialog(
            self, message="Save file as ...", defaultDir=initPath, 
            defaultFile=filename, style=wx.SAVE, wildcard=wildcard)
        if dlg.ShowModal() == wx.ID_OK:
            newPath = dlg.GetPath()
            self.fileSave(event=None, filename=newPath)
            self.currentDoc.filename = newPath
            path, shortName = os.path.split(newPath)
            self.notebook.SetPageText(self.notebook.GetSelection(), shortName)
            self.setFileModified(False)
        try: #this seems correct on PC, but not on mac   
            dlg.destroy()
        except:
            pass
    def fileClose(self, event, filename=None):        
        if filename==None:
            filename = self.currentDoc.filename
        self.currentDoc = self.notebook.GetPage(self.notebook.GetSelection())
        if self.currentDoc.UNSAVED==True:
            sys.stdout.flush()
            dlg = wx.MessageDialog(self, message='Save changes to %s before quitting?' %filename,
                caption='Warning', style=wx.YES_NO|wx.CANCEL )
            resp = dlg.ShowModal()
            sys.stdout.flush()
            dlg.Destroy()
            if resp  == wx.ID_CANCEL:
                return -1 #return, don't quit
            elif resp == wx.ID_YES:
                #save then quit
                self.fileSave(None)
            elif resp == wx.ID_NO:
                pass #don't save just quit
        #remove the document and its record
        currId = self.notebook.GetSelection()
        #if this was called by AuiNotebookEvent, then page has closed already
        if not isinstance(event, wx.aui.AuiNotebookEvent):
            self.notebook.DeletePage(currId)
        #set new current doc
        newPageID = self.notebook.GetSelection()
        if newPageID == -1:
            self.currentDoc = None
            self.SetLabel("PsychoPy (Coder) (v%s)" %self.app.version)
        else:
            self.currentDoc = self.notebook.GetPage(newPageID)
            self.setFileModified(self.currentDoc.UNSAVED)#set to current file status
        #return 1
    def _runFileAsImport(self):      
        fullPath = self.currentDoc.filename
        path, scriptName = os.path.split(fullPath)
        importName, ext = os.path.splitext(scriptName)
        #set the directory and add to path
        os.chdir(path)
        sys.path.insert(0, path) 
        
        #update toolbar
        self.toolbar.EnableTool(self.IDs.tbRun,False)
        self.toolbar.EnableTool(self.IDs.tbStop,True)
        
        #do an 'import' on the file to run it
        if importName in sys.modules: #delete the sys reference to it (so we think its a new import)
            sys.modules.pop(importName)
        exec('import %s' %(importName)) #or run first time    
            
    
    def _runFileInDbg(self):
        #setup a debugger and then runFileAsImport
        fullPath = self.currentDoc.filename
        path, scriptName = os.path.split(fullPath)
        importName, ext = os.path.splitext(scriptName)
        #set the directory and add to path
        os.chdir(path)
        
        self.db = PsychoDebugger()
        #self.db.set_break(fullPath, 8)
        #print self.db.get_file_breaks(fullPath)
        self.db.runcall(self._runFileAsImport)   
                
    def _runFileAsProcess(self):
        fullPath = self.currentDoc.filename
        path, scriptName = os.path.split(fullPath)
        importName, ext = os.path.splitext(scriptName)
        #set the directory and add to path
        os.chdir(path)
        self.scriptProcess=wx.Process(self) #self is the parent (which will receive an event when the process ends)
        self.scriptProcess.Redirect()#catch the stdout/stdin
        
        if sys.platform=='win32':
            command = '"%s" -u "%s"' %(sys.executable, fullPath)# the quotes allow file paths with spaces
            #self.scriptProcessID = wx.Execute(command, wx.EXEC_ASYNC, self.scriptProcess)
            self.scriptProcessID = wx.Execute(command, wx.EXEC_ASYNC| wx.EXEC_NOHIDE, self.scriptProcess)
        else:  
            fullPath= fullPath.replace(' ','\ ')
            command = '%s -u %s' %(sys.executable, fullPath)# the quotes would break a unix system command
            self.scriptProcessID = wx.Execute(command, wx.EXEC_ASYNC| wx.EXEC_MAKE_GROUP_LEADER, self.scriptProcess)
        self.toolbar.EnableTool(self.IDs.tbRun,False)
        self.toolbar.EnableTool(self.IDs.tbStop,True)             
        
    def runFile(self, event):
        """Runs files by one of various methods
        """
        fullPath = self.currentDoc.filename
        #check syntax by compiling - errors printed (not raised as error)
        try:
            py_compile.compile(fullPath, doraise=False)
        except Exception, e:
            print "Problem compiling: %s" %e
        
        print '\nRunning %s as %s' %(self.currentDoc.filename, self.appPrefs['runScripts']) 
        self.ignoreErrors = False
        self.SetEvtHandlerEnabled(False)
        wx.EVT_IDLE(self, None)
        
        #try to run script
        try:# try to capture any errors in the script
            if self.appPrefs['runScripts'] == 'thread':
                self.thread = ScriptThread(target= self._runFileAsImport, gui=self)
                self.thread.start()
            elif self.appPrefs['runScripts']=='process':          
                self._runFileAsProcess()
            
            elif self.appPrefs['runScripts']=='dbg':            
                #create a thread and run file as debug within that thread
                self.thread = ScriptThread(target= self._runFileInDbg, gui=self)
                self.thread.start()
            elif self.appPrefs['runScripts']=='import':
                #simplest possible way, but fragile
                #USING import of scripts (clunky)                
                if importName in sys.modules: #delete the sys reference to it
                    sys.modules.pop(importName)
                exec('import %s' %(importName)) #or run first time                
                    #NB execfile() would be better doesn't run the import statements properly!
                    #functions defined in the script have a separate namespace to the main
                    #body of the script(!?)
                    #execfile(thisFile)                        
        except SystemExit:#this is used in psychopy.core.quit()
            pass
        except: #report any errors that came up
            if self.ignoreErrors:
                pass
            else:
                #traceback.print_exc()
                #tb = traceback.extract_tb(sys.last_traceback)
                #for err in tb:
                #    print '%s, line:%i,function:%s\n%s' %tuple(err)
                print ''#print a new line 
             
        self.SetEvtHandlerEnabled(True)  
        wx.EVT_IDLE(self, self.onIdle) 
        
    def stopFile(self, event):
        self.toolbar.EnableTool(self.IDs.tbRun,True)
        self.toolbar.EnableTool(self.IDs.tbStop,False)
        if self.appPrefs['runScripts'] in ['thread','dbg']:
            #killing a debug context doesn't really work on pygame scripts because of the extra 
            if self.appPrefs['runScripts'] == 'dbg':self.db.quit()
            try:
                pygame.display.quit()#if pygame is running then try to kill it
            except:
                pass
            self.thread.kill()
            self.ignoreErrors = False#stop listening for errors if the script has ended
        elif self.appPrefs['runScripts']=='process':
            success = wx.Kill(self.scriptProcessID,wx.SIGTERM) #try to kill it gently first
            if success[0] != wx.KILL_OK:
                wx.Kill(self.scriptProcessID,wx.SIGKILL) #kill it aggressively        
        
        
    def copy(self, event):
        foc= self.FindFocus()
        foc.Copy()
        #if isinstance(foc, CodeEditor):
        #    self.currentDoc.Copy()#let the text ctrl handle this
        #elif isinstance(foc, StdOutRich):
        
    def duplicateLine(self,event):
        self.currentDoc.LineDuplicate()
    def cut(self, event):
        self.currentDoc.Cut()#let the text ctrl handle this
    def paste(self, event):
        foc= self.FindFocus()
        foc.Paste()
    def undo(self, event):
        self.currentDoc.Undo()
    def redo(self, event):
        self.currentDoc.Redo()
    def commentSelected(self,event):
        self.currentDoc.commentLines()
    def uncommentSelected(self, event):
        self.currentDoc.uncommentLines()
    def foldAll(self, event):
        self.currentDoc.FoldAll()
    #def unfoldAll(self, event):
        #self.currentDoc.ToggleFoldAll(expand = False)
    def setOutputWindow(self, event):      
        #show/hide the output window (from the view menu control)  
        if self.outputChk.IsChecked():
            #show the pane
            self.prefs['showOutput']=True
            self.paneManager.GetPane('Output').Show()
            #will we actually redirect the output?
            sys.stdout = self.outputWindow
            sys.stderr = self.outputWindow
        else:
            #show the pane
            self.prefs['showOutput']=False
            self.paneManager.GetPane('Output').Hide()
            sys.stdout = self._origStdOut#discovered during __init__
            sys.stderr = self._origStdErr
            
        self.paneManager.Update()    
        
    def setSourceAsst(self, event):
        #show/hide the source assistant (from the view menu control)
        if not self.sourceAsstChk.IsChecked():
            self.paneManager.GetPane("SourceAsst").Hide()
            self.prefs['showSourceAsst']=False
        else:
            self.paneManager.GetPane("SourceAsst").Show()
            self.prefs['showSourceAsst']=True
        self.paneManager.Update()
    def analyseCodeNow(self, event):
        self.SetStatusText('analysing code')
        if self.currentDoc is not None:
            self.currentDoc.analyseScript() ###todo: check if we HAVE a proper currentDoc, with the ability to analyse
        else:
            print 'Open a file from the File menu, or drag one onto this app, or open a demo from the Help menu'
            
        self.SetStatusText('ready')
    def setAnalyseAuto(self, event):
        #set autoanalysis (from the check control in the tools menu)
        if self.analyseAutoChk.IsChecked():
            self.prefs['analyseAuto']=True
        else:
            self.prefs['analyseAuto']=False
    def openMonitorCenter(self,event):
        from monitors import MonitorCenter
        frame = MonitorCenter.MainFrame(None,'PsychoPy Monitor Centre')
        frame.Show(True)
    def loadDemo(self, event):
        self.setCurrentDoc( self.demos[event.GetId()] )
    def tabKeyPressed(self,event):
        #if several chars are selected then smartIndent
        #if we're at the start of the line then smartIndent
        if self.currentDoc.shouldTrySmartIndent():
            self.smartIndent(event = None)
        else:
            #self.currentDoc.CmdKeyExecute(wx.stc.STC_CMD_TAB)
            pos = self.currentDoc.GetCurrentPos()
            self.currentDoc.InsertText(pos ,'\t')
            self.currentDoc.SetCurrentPos(pos+1)
            self.currentDoc.SetSelection(pos+1, pos+1)
    def smartIndent(self, event):
        self.currentDoc.smartIndent()
    def indent(self, event):
        self.currentDoc.indentSelection(4)
    def dedent(self, event):
        self.currentDoc.indentSelection(-4)
    def setFileModified(self, isModified):
        #changes the document flag, updates save buttons
        self.currentDoc.UNSAVED=isModified
        self.toolbar.EnableTool(self.IDs.tbFileSave, isModified)#disabled when not modified
        self.fileMenu.Enable(self.fileMenu.FindItem('&Save\tCtrl+S"'), isModified)
    def onProcessEnded(self, event):
        self.onIdle(event=None)#this is will check the stdout and stderr for any last messages
        self.scriptProcess=None
        self.scriptProcessID=None        
        self.toolbar.EnableTool(self.IDs.tbRun,True)
        self.toolbar.EnableTool(self.IDs.tbStop,False)