import requests

def main():
    try:
        r = requests.post('http://127.0.0.1:8000/analyze', files={'files': ('test.txt', 'hello', 'text/plain')})
        print('status', r.status_code)
        print('location', r.headers.get('Location'))
        print(r.text[:400])
    except Exception as e:
        print('ERROR', e)

if __name__ == '__main__':
    main()
