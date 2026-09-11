#include <windows.h>
#include <stdio.h>
static void dump(HWND h, int depth)
{
    char cls[64] = "", text[64] = ""; RECT r = {0}; DWORD pid = 0;
    GetClassNameA(h, cls, sizeof(cls)); GetWindowTextA(h, text, sizeof(text)); GetWindowRect(h, &r);
    GetWindowThreadProcessId(h, &pid);
    printf("%*shwnd %p pid %lu style %08lx ex %08lx parent %p owner %p vis %d rect %ld,%ld-%ld,%ld class '%s' text '%s'\n", depth * 2, "", h, pid,
           GetWindowLongW(h, GWL_STYLE), GetWindowLongW(h, GWL_EXSTYLE), GetAncestor(h, GA_PARENT), GetWindow(h, GW_OWNER),
           IsWindowVisible(h), r.left, r.top, r.right, r.bottom, cls, text);
    for (HWND c = GetWindow(h, GW_CHILD); c; c = GetWindow(c, GW_HWNDNEXT)) if (depth < 6) dump(c, depth + 1);
}
static BOOL CALLBACK cb(HWND h, LPARAM l) { dump(h, 0); return TRUE; }
int main(void) { EnumWindows(cb, 0); return 0; }
