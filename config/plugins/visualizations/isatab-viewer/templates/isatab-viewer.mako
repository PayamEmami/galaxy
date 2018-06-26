<!DOCTYPE HTML>
<%

    ## generates hash (hdadict['id']) of history item
    hdadict = trans.security.encode_dict_ids( hda.to_dict() )

    ## finds the parent directory of galaxy ( /, /galaxy, etc.)
    root = h.url_for( '/' )

    ## determines the exposed URL  of the ./static/ directory
    app_root = root + 'plugins/visualizations/' + visualization_name + '/static'

    ## actual file URL:
    file_url =  root + 'datasets/' + hdadict['id'] + "/display?to_ext=" + hda.ext;
%>
<html>
    <head lang="en">
        <meta charset="UTF-8">
        <title>${hda.name | h} | ${visualization_name}</title>
    </head>
    <body>
        Blank
    </body>
</html>