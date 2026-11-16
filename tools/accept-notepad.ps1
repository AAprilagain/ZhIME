param([ValidateSet('type','read','capture','save','close','close-test-tab','exercise','commit','mode-probe','menu','cancel','punctuation','suggestion','select-1','continue','popup-visible','focus-away','switch-ime')][string]$Action='read',[string]$WindowTitleContains='zhime-acceptance.txt',[string]$CaptureName='notepad-acceptance.png',[switch]$SkipSave,[ValidateRange(0,9)][int]$MenuIndex=0,[ValidatePattern('^[a-zA-Z]+$')][string]$Pinyin='jintianwanshang')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Text;
public static class ZhImeGui {
 [StructLayout(LayoutKind.Sequential)] public struct K { public ushort vk,scan; public uint flags,time; public UIntPtr extra; }
 [StructLayout(LayoutKind.Sequential)] public struct M { public int x,y; public uint data,flags,time; public UIntPtr extra; }
 [StructLayout(LayoutKind.Explicit,Size=40)] public struct I { [FieldOffset(0)] public uint type; [FieldOffset(8)] public K key; [FieldOffset(8)] public M mouse; }
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int left,top,right,bottom; }
 [DllImport("user32")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32")] static extern bool IsIconic(IntPtr h);
 [DllImport("user32")] public static extern bool ShowWindowAsync(IntPtr h,int command);
 [DllImport("user32")] static extern bool AttachThreadInput(uint first,uint second,bool attach);
 [DllImport("kernel32")] static extern uint GetCurrentThreadId();
 [DllImport("user32")] public static extern bool GetWindowRect(IntPtr h,out Rect r);
 delegate bool EnumProc(IntPtr h,IntPtr p);
 [DllImport("user32")] static extern bool EnumWindows(EnumProc callback,IntPtr p);
 [DllImport("user32",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder text,int count);
 [DllImport("user32",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder text,int count);
 [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint process);
 [DllImport("user32")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32")] static extern bool PostMessage(IntPtr h,uint msg,IntPtr w,IntPtr l);
 [DllImport("user32")] static extern IntPtr SendMessage(IntPtr h,uint msg,IntPtr w,IntPtr l);
 [DllImport("user32")] static extern int GetMenuItemCount(IntPtr menu);
 [DllImport("user32")] static extern uint GetMenuItemID(IntPtr menu,int index);
 [DllImport("user32")] static extern bool GetMenuItemRect(IntPtr window,IntPtr menu,uint index,out Rect r);
 [DllImport("user32")] static extern bool SetCursorPos(int x,int y);
 [StructLayout(LayoutKind.Sequential)] struct Point { public int x,y; }
 [DllImport("user32")] static extern bool GetCursorPos(out Point p);
 public static IntPtr FindTest(string fragment) { IntPtr result=IntPtr.Zero; EnumWindows((h,p)=>{if(!IsWindowVisible(h))return true;var s=new StringBuilder(1024);GetWindowText(h,s,1024);if(s.ToString().Contains(fragment)){result=h;return false;}return true;},IntPtr.Zero);return result; }
 public static void Menu(IntPtr main,int index) { uint owner;GetWindowThreadProcessId(main,out owner);IntPtr candidate=IntPtr.Zero;EnumWindows((h,p)=>{uint pid;GetWindowThreadProcessId(h,out pid);var s=new StringBuilder(256);GetClassName(h,s,256);if(pid==owner&&IsWindowVisible(h)&&s.ToString()=="ZhIME.Candidates"){candidate=h;return false;}return true;},IntPtr.Zero);if(candidate==IntPtr.Zero)throw new Exception("Visible candidate popup not found");Guard(main);PostMessage(candidate,0x007b,candidate,new IntPtr(-1));Thread.Sleep(250);uint foreground;GetWindowThreadProcessId(GetForegroundWindow(),out foreground);if(foreground!=owner)throw new Exception("Test menu lost focus");Press(0x24);for(int i=0;i<index;i++)Press(0x28);Press(0x0d);Thread.Sleep(300); }
 public static uint Owner(IntPtr main) { uint p;GetWindowThreadProcessId(main,out p);return p; }
 public static bool HasPopup(IntPtr main) {
   uint owner=Owner(main);bool visible=false;
   EnumWindows((h,p)=>{var name=new StringBuilder(256);GetClassName(h,name,256);
     if(Owner(h)==owner&&IsWindowVisible(h)&&name.ToString()=="ZhIME.Candidates"){visible=true;return false;}return true;},IntPtr.Zero);
   return visible;
 }
 public static void FocusTest(IntPtr target) {
   if(IsIconic(target)) ShowWindowAsync(target,9);
   if(SetForegroundWindow(target)) return;
   uint ignored;
   uint foreground=GetWindowThreadProcessId(GetForegroundWindow(),out ignored);
   uint current=GetCurrentThreadId();
   if(foreground!=0 && foreground!=current && AttachThreadInput(current,foreground,true)){
     try { SetForegroundWindow(target); } finally { AttachThreadInput(current,foreground,false); }
   }
 }
 public static void OpenMenu(IntPtr main) { uint owner=Owner(main);IntPtr candidate=IntPtr.Zero;EnumWindows((h,p)=>{var s=new StringBuilder(256);GetClassName(h,s,256);if(Owner(h)==owner&&IsWindowVisible(h)&&s.ToString()=="ZhIME.Candidates"){candidate=h;return false;}return true;},IntPtr.Zero);if(candidate==IntPtr.Zero)throw new Exception("Visible candidate popup not found");Guard(main);PostMessage(candidate,0x007b,candidate,new IntPtr(-1));Thread.Sleep(250); }
 public static void ClickMenu(IntPtr main,uint command) { uint owner=Owner(main);IntPtr popup=IntPtr.Zero;for(int retry=0;retry<20&&popup==IntPtr.Zero;retry++){EnumWindows((h,p)=>{var s=new StringBuilder(256);GetClassName(h,s,256);if(Owner(h)==owner&&IsWindowVisible(h)&&s.ToString()=="#32768"){popup=h;return false;}return true;},IntPtr.Zero);if(popup==IntPtr.Zero)Thread.Sleep(50);}if(popup==IntPtr.Zero)throw new Exception("Test popup menu not found");var menu=SendMessage(popup,0x01e1,IntPtr.Zero,IntPtr.Zero);for(int i=0;i<GetMenuItemCount(menu);i++){if(GetMenuItemID(menu,i)!=command)continue;Rect r;if(!GetMenuItemRect(IntPtr.Zero,menu,(uint)i,out r))throw new Exception("Menu item rectangle unavailable");if(Owner(GetForegroundWindow())!=owner)throw new Exception("Menu lost foreground ownership");Point old;GetCursorPos(out old);SetCursorPos((r.left+r.right)/2,(r.top+r.bottom)/2);var down=new I();down.mouse.flags=2;var up=new I();up.mouse.flags=4;if(SendInput(2,new[]{down,up},40)!=2)throw new Exception("Menu click failed");Thread.Sleep(250);SetCursorPos(old.x,old.y);return;}throw new Exception("Expected menu command not present"); }
 [DllImport("user32",SetLastError=true)] static extern uint SendInput(uint n,I[] inputs,int size);
 public static void Key(ushort vk,bool up=false) { var x=new I();x.type=1;x.key.vk=vk;x.key.flags=up?2u:0u;if(SendInput(1,new[]{x},40)!=1)throw new Exception("SendInput failed"); }
 public static void Press(ushort vk) { Key(vk);Key(vk,true);Thread.Sleep(90); }
 public static void Guard(IntPtr expected) { var active=GetForegroundWindow();if(active!=expected)throw new Exception("Test window lost focus; no keys sent (target PID "+Owner(expected)+", foreground PID "+Owner(active)+")"); }
}
'@
$hwnd=[ZhImeGui]::FindTest($WindowTitleContains)
if ($hwnd -eq [IntPtr]::Zero) { throw 'Dedicated acceptance Notepad window not found.' }
if ($Action -in @('type','save','close','close-test-tab','exercise','commit','capture','mode-probe','menu','cancel','punctuation','suggestion','select-1','continue','switch-ime')) {
    [ZhImeGui]::FocusTest($hwnd)
    Start-Sleep -Milliseconds 250
    [ZhImeGui]::Guard($hwnd)
}
if ($Action -eq 'switch-ime') {
    [ZhImeGui]::Key(0x5B)
    try { [ZhImeGui]::Press(0x20) } finally { [ZhImeGui]::Key(0x5B,$true) }
}
if ($Action -eq 'type') {
    [ZhImeGui]::Key(0x11); [ZhImeGui]::Press(0x23); [ZhImeGui]::Key(0x11,$true)
    foreach ($key in @(0x4E,0x49,0x48,0x41,0x4F)) { [ZhImeGui]::Guard($hwnd); [ZhImeGui]::Press($key) }
    Start-Sleep -Milliseconds 1500
    Write-Host 'Typed nihao into dedicated Notepad test window; pending composition retained.'
}
if ($Action -eq 'save') {
    [ZhImeGui]::Press(0x20);Start-Sleep -Milliseconds 500
    [ZhImeGui]::Guard($hwnd);[ZhImeGui]::Key(0x11);[ZhImeGui]::Press(0x53);[ZhImeGui]::Key(0x11,$true)
}
if ($Action -eq 'commit') { [ZhImeGui]::Press(0x20);Start-Sleep -Milliseconds 500 }
if ($Action -eq 'suggestion') {
    foreach ($key in $Pinyin.ToUpperInvariant().ToCharArray()) { [ZhImeGui]::Guard($hwnd); [ZhImeGui]::Press([ushort]$key) }
    Start-Sleep -Milliseconds 800
    [ZhImeGui]::Guard($hwnd); [ZhImeGui]::Press(0x20)
    Start-Sleep -Milliseconds 500
}
if ($Action -eq 'select-1') { [ZhImeGui]::Press(0x31); Start-Sleep -Milliseconds 500 }
if ($Action -eq 'continue') { [ZhImeGui]::Press(0x4E); Start-Sleep -Milliseconds 500 }
if ($Action -eq 'popup-visible') { [ZhImeGui]::HasPopup($hwnd) }
if ($Action -eq 'focus-away') {
    [ZhImeGui]::Guard($hwnd)
    [ZhImeGui]::ShowWindowAsync($hwnd,6) | Out-Null
    Start-Sleep -Milliseconds 500
    if ([ZhImeGui]::GetForegroundWindow() -eq $hwnd) { throw 'Focus did not move' }
    if ([ZhImeGui]::HasPopup($hwnd)) { throw 'Candidate window survived focus loss' }
}
if ($Action -eq 'menu') {
    [ZhImeGui]::OpenMenu($hwnd)
    $commands=@(100,101,102,103,104,105,201,202,200,203)
    [ZhImeGui]::ClickMenu($hwnd,$commands[$MenuIndex])
    Start-Sleep -Milliseconds 300
}
if ($Action -eq 'cancel') { [ZhImeGui]::Press(0x1B) }
if ($Action -eq 'punctuation') { [ZhImeGui]::Press(0xBC);[ZhImeGui]::Press(0xBE) }
if ($Action -eq 'mode-probe') {
    [ZhImeGui]::Press(0x10)
    foreach($k in @(0x41,0x42,0x43)){[ZhImeGui]::Guard($hwnd);[ZhImeGui]::Press($k)}
    [ZhImeGui]::Press(0x1B)
    [ZhImeGui]::Press(0x10)
    foreach($k in @(0x4E,0x49,0x48,0x41,0x4F)){[ZhImeGui]::Guard($hwnd);[ZhImeGui]::Press($k)}
    Start-Sleep -Milliseconds 800;[ZhImeGui]::Press(0x20)
}
if ($Action -eq 'exercise') {
    function Press([int]$key) { [ZhImeGui]::Guard($hwnd);[ZhImeGui]::Press([ushort]$key) }
    function Chord([int[]]$modifiers,[int]$key) { [ZhImeGui]::Guard($hwnd);foreach($m in $modifiers){[ZhImeGui]::Key([ushort]$m)};Press $key;foreach($m in $modifiers){[ZhImeGui]::Key([ushort]$m,$true)} }
    function TypePinyin([string]$text) { foreach($c in $text.ToUpper().ToCharArray()){Press ([int]$c)};Start-Sleep -Milliseconds 800 }
    Chord @(0x11) 0x23
    TypePinyin 'nihao';Press 0x25;Press 0x08;Press 0x41;Press 0x23;Start-Sleep -Milliseconds 800;Press 0x20
    TypePinyin 'ceshi';Press 0x1B
    TypePinyin 'nihao';Press 0x32
    TypePinyin 'shi';Press 0x22;Press 0x31
    Press 0xBC;Press 0xBE
    Chord @(0x11,0x10) 0x50;Press 0xBC;Press 0xBE;Chord @(0x11,0x10) 0x50
    Chord @(0x11) 0x20;foreach($k in @(0x41,0x42,0x43)){Press $k};Chord @(0x11) 0x20
    Chord @(0x11,0x12) 0x34;TypePinyin 'lanjing';Press 0x1B;Chord @(0x11,0x12) 0x34
    Chord @(0x11,0x10) 0x08
    if (!$SkipSave) { Chord @(0x11) 0x53 }
    Write-Host 'Executed guarded editing, cancel, numeric selection, page selection, punctuation, English and fuzzy-toggle sequences in the dedicated test document.'
}
if ($Action -eq 'capture') {
    $rect=[ZhImeGui+Rect]::new();[ZhImeGui]::GetWindowRect($hwnd,[ref]$rect) | Out-Null
    $bmp=[Drawing.Bitmap]::new($rect.right-$rect.left,$rect.bottom-$rect.top)
    $graphics=[Drawing.Graphics]::FromImage($bmp);$graphics.CopyFromScreen($rect.left,$rect.top,0,0,$bmp.Size)
    $bmp.Save((Join-Path (Split-Path $PSScriptRoot -Parent) ('runtime\'+$CaptureName)));$graphics.Dispose();$bmp.Dispose()
}
if ($Action -eq 'close') {
    [ZhImeGui]::Key(0x12);[ZhImeGui]::Press(0x73);[ZhImeGui]::Key(0x12,$true)
}
if ($Action -eq 'close-test-tab') {
    foreach ($key in @(0x53,0x57)) {
        if ([ZhImeGui]::FindTest($WindowTitleContains) -ne $hwnd) { throw 'Test tab changed' }
        [ZhImeGui]::Guard($hwnd)
        [ZhImeGui]::Key(0x11)
        try { [ZhImeGui]::Press($key) } finally { [ZhImeGui]::Key(0x11,$true) }
    }
}
if ($Action -eq 'read') {
    $element=[Windows.Automation.AutomationElement]::FromHandle($hwnd)
    $all=$element.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {
        if ($e.Current.ControlType -eq [Windows.Automation.ControlType]::Document) {
            try { $pattern=$e.GetCurrentPattern([Windows.Automation.TextPattern]::Pattern); $pattern.DocumentRange.GetText(-1) } catch {}
        }
    }
}
