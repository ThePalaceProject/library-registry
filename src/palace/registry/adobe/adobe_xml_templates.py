ACCOUNT_INFO_REQUEST_TEMPLATE = """<accountInfoRequest method="standard" xmlns="http://ns.adobe.com/adept">
<user>%(uuid)s</user>
</accountInfoRequest >"""

ACCOUNT_INFO_RESPONSE_TEMPLATE = """<accountInfoResponse xmlns="http://ns.adobe.com/adept">
    <label>%(label)s</label>
</accountInfoResponse>"""

ERROR_RESPONSE_TEMPLATE = '<error xmlns="http://ns.adobe.com/adept" data="E_%(vendor_id)s_%(type)s %(message)s"/>'

AUTHDATA_SIGN_IN_REQUEST_TEMPLATE = """<signInRequest method="authData" xmlns="http://ns.adobe.com/adept">
<authData>%(authdata)s</authData>
</signInRequest>"""

SIGN_IN_REQUEST_TEMPLATE = """<signInRequest method="standard" xmlns="http://ns.adobe.com/adept">
    <username>%(username)s</username>
    <password>%(password)s</password>
</signInRequest>"""

SIGN_IN_RESPONSE_TEMPLATE = """<signInResponse xmlns="http://ns.adobe.com/adept">
    <user>%(user)s</user>
    <label>%(label)s</label>
</signInResponse>"""
