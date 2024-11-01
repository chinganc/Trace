:py:mod:`opto.trace.utils`
==========================

.. py:module:: opto.trace.utils

.. autodoc2-docstring:: opto.trace.utils
   :allowtitles:

Module Contents
---------------

Functions
~~~~~~~~~

.. list-table::
   :class: autosummary longtable
   :align: left

   * - :py:obj:`sum_feedback <opto.trace.utils.sum_feedback>`
     - .. autodoc2-docstring:: opto.trace.utils.sum_feedback
          :summary:
   * - :py:obj:`contain <opto.trace.utils.contain>`
     - .. autodoc2-docstring:: opto.trace.utils.contain
          :summary:
   * - :py:obj:`parse_eqs_to_dict <opto.trace.utils.parse_eqs_to_dict>`
     - .. autodoc2-docstring:: opto.trace.utils.parse_eqs_to_dict
          :summary:
   * - :py:obj:`for_all_methods <opto.trace.utils.for_all_methods>`
     - .. autodoc2-docstring:: opto.trace.utils.for_all_methods
          :summary:
   * - :py:obj:`render_opt_step <opto.trace.utils.render_opt_step>`
     - .. autodoc2-docstring:: opto.trace.utils.render_opt_step
          :summary:
   * - :py:obj:`escape_json_nested_quotes <opto.trace.utils.escape_json_nested_quotes>`
     - .. autodoc2-docstring:: opto.trace.utils.escape_json_nested_quotes
          :summary:
   * - :py:obj:`remove_non_ascii <opto.trace.utils.remove_non_ascii>`
     - .. autodoc2-docstring:: opto.trace.utils.remove_non_ascii
          :summary:
   * - :py:obj:`test_json_quote_escaper <opto.trace.utils.test_json_quote_escaper>`
     - .. autodoc2-docstring:: opto.trace.utils.test_json_quote_escaper
          :summary:

Data
~~~~

.. list-table::
   :class: autosummary longtable
   :align: left

   * - :py:obj:`builtins_list <opto.trace.utils.builtins_list>`
     - .. autodoc2-docstring:: opto.trace.utils.builtins_list
          :summary:
   * - :py:obj:`global_functions_list <opto.trace.utils.global_functions_list>`
     - .. autodoc2-docstring:: opto.trace.utils.global_functions_list
          :summary:

API
~~~

.. py:data:: builtins_list
   :canonical: opto.trace.utils.builtins_list
   :value: 'dir(...)'

   .. autodoc2-docstring:: opto.trace.utils.builtins_list

.. py:data:: global_functions_list
   :canonical: opto.trace.utils.global_functions_list
   :value: None

   .. autodoc2-docstring:: opto.trace.utils.global_functions_list

.. py:function:: sum_feedback(nodes)
   :canonical: opto.trace.utils.sum_feedback

   .. autodoc2-docstring:: opto.trace.utils.sum_feedback

.. py:function:: contain(container_of_nodes, node)
   :canonical: opto.trace.utils.contain

   .. autodoc2-docstring:: opto.trace.utils.contain

.. py:function:: parse_eqs_to_dict(text)
   :canonical: opto.trace.utils.parse_eqs_to_dict

   .. autodoc2-docstring:: opto.trace.utils.parse_eqs_to_dict

.. py:function:: for_all_methods(decorator)
   :canonical: opto.trace.utils.for_all_methods

   .. autodoc2-docstring:: opto.trace.utils.for_all_methods

.. py:function:: render_opt_step(step_idx, optimizer, no_trace_graph=False, no_improvement=False)
   :canonical: opto.trace.utils.render_opt_step

   .. autodoc2-docstring:: opto.trace.utils.render_opt_step

.. py:function:: escape_json_nested_quotes(json_str)
   :canonical: opto.trace.utils.escape_json_nested_quotes

   .. autodoc2-docstring:: opto.trace.utils.escape_json_nested_quotes

.. py:function:: remove_non_ascii(json_txt)
   :canonical: opto.trace.utils.remove_non_ascii

   .. autodoc2-docstring:: opto.trace.utils.remove_non_ascii

.. py:function:: test_json_quote_escaper()
   :canonical: opto.trace.utils.test_json_quote_escaper

   .. autodoc2-docstring:: opto.trace.utils.test_json_quote_escaper
