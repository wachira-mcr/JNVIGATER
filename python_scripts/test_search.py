import urllib.request, urllib.parse, re
url = 'https://lite.duckduckgo.com/lite/'
data = urllib.parse.urlencode({'q': 'mtputty password decryption python'}).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    links = re.findall(r'<a[^>]+href=\"([^\"]+)\"[^>]*class=\"result-url\"', html)
    for link in links[:5]:
        print(link)
except Exception as e:
    print('Error:', e)
