from galaxy.datatypes.text import Text


class NMRProcFlow(Text):

    composite_type = 'auto_primary_file'

    def __init__ ( self, **kwd ):
        Text.__init__(self, **kwd)
        self.add_composite_file('samples.csv')
        self.add_composite_file('factors')
        self.add_composite_file('nuc.txt')
        self.add_composite_file('SpecProcpar.ini')
        self.add_composite_file('list_pars.csv')
        self.add_composite_file('specs.pack', is_binary=True)
        self.add_composite_file('SpecPreProcCmd.lst')
        self.add_composite_file('SpecProcCmd.lst', optional=True)
        # apparently missing Zones<x>_list.ln file, doesn't seem to be needed for nv

    def generate_primary_file( self, dataset = None ):
        rval = ['<html><head><title>Files for Composite Dataset (%s)</title></head><p/>This composite dataset is composed of the following files:<p/><ul>' % ( self.file_ext )]
        for composite_name, composite_file in self.get_composite_files( dataset = dataset ).iteritems():
            opt_text = ''
            if composite_file.optional:
                opt_text = ' (optional)'
            rval.append( '<li><a href="%s">%s</a>%s' % ( composite_name, composite_name, opt_text ) )
        rval.append( '</ul></html>' )
        return "\n".join( rval )