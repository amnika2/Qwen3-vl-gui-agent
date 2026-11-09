from types import SimpleNamespace

from guiqa.oracle import request_fields


def test_multipart_preserves_localized_amount_and_ignores_encoding_details():
    body = (b'--test\r\nContent-Disposition: form-data; name="sale_price"\r\n\r\n1.234.567,89\r\n'
            b'--test\r\nContent-Disposition: form-data; name="name"\r\n\r\nCeramic Table Lamp\r\n--test--\r\n')
    request = SimpleNamespace(headers={"content-type": "multipart/form-data; boundary=test"}, post_data_buffer=body)
    fields = request_fields(request)
    assert fields == {"sale_price": "1.234.567,89", "name": "Ceramic Table Lamp"}


def test_urlencoded_nested_provider_fields():
    request = SimpleNamespace(headers={"content-type": "application/x-www-form-urlencoded"},
        post_data="provider%5Bid%5D=2&provider%5Bmobile_number%5D=%2B15551234567")
    assert request_fields(request) == {"provider[id]": "2", "provider[mobile_number]": "+15551234567"}
