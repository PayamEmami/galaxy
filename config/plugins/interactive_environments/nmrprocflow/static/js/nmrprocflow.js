function load_nmrprocflow(url){
    $( document ).ready(function() {
        test_ie_availability(url, function(){
            append_notebook(url)
        });
    });
}

function append_notebook(url){
    IES.clear_main_area();
    $('#main').append(
        '<iframe frameBorder="0" seamless="seamless" style="width: 100%; height: 100%; overflow:auto;" scrolling="yes" src="'+ url +'"></iframe>'
    );
}