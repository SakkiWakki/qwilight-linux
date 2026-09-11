#define COBJMACROS
#include <windows.h>
#include <stdio.h>
#include <initguid.h>
#include <dcomp.h>
int main(void)
{
    IDCompositionDevice *dev; IDCompositionVisual *a, *b, *c; HRESULT hr;
    hr = DCompositionCreateDevice(NULL, &IID_IDCompositionDevice, (void **)&dev);
    printf("device hr %#lx\n", hr); if (FAILED(hr)) return 1;
    IDCompositionDevice_CreateVisual(dev, &a); IDCompositionDevice_CreateVisual(dev, &b); IDCompositionDevice_CreateVisual(dev, &c);
    printf("a %p b %p c %p\n", a, b, c);
    hr = IDCompositionVisual_AddVisual(a, c, FALSE, NULL); printf("a.Add(c) %#lx\n", hr);
    hr = IDCompositionVisual_AddVisual(b, c, TRUE, NULL); printf("b.Add(c) %#lx (reparent)\n", hr);
    hr = IDCompositionVisual_RemoveAllVisuals(c); printf("c.RemoveAll %#lx\n", hr);
    hr = IDCompositionVisual_AddVisual(c, a, TRUE, NULL); printf("c.Add(a) %#lx\n", hr);
    hr = IDCompositionVisual_AddVisual(a, b, TRUE, NULL); printf("a.Add(b) %#lx (cycle: b>c>a>b)\n", hr);
    return 0;
}
